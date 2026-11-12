"""INT8 Scalar Quantization & Asymmetric Float32 Rescoring (ADR Phase 6 Section 5)."""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass

from talent_screener.infrastructure.persistence.vector_store.in_memory_backend import (
    ChildVectorPoint,
)


@dataclass(frozen=True, slots=True)
class QuantizedVectorPayload:
    """Compact INT8 scalar-quantized vector bundle paired with exact float32 copy."""

    point: ChildVectorPoint
    quantized_int8: bytes
    dimensions: int
    quantization_scale: float = 127.0


@dataclass(frozen=True, slots=True)
class QuantizedSearchHit:
    """Two-stage retrieval result combining INT8 pre-filter and exact float32 scores."""

    point: ChildVectorPoint
    quantized_approx_score: float
    exact_float32_score: float


class Int8ScalarQuantizer:
    """Scalar quantizer mapping unit-normalized float32 embeddings into signed int8."""

    _SCALE: float = 127.0

    @staticmethod
    def l2_normalize(vector: Sequence[float]) -> tuple[float, ...]:
        """Return unit L2-normalized tuple of floats."""
        if not vector:
            return ()
        sq_sum = sum(float(x) * float(x) for x in vector)
        norm = math.sqrt(sq_sum)
        if norm <= 1e-12:
            return tuple(0.0 for _ in vector)
        return tuple(float(x) / norm for x in vector)

    @classmethod
    def quantize(cls, vector: Sequence[float], *, normalize: bool = True) -> bytes:
        """Compute q_i = clip(round(127 * x_i), -127, 127) and pack into signed int8 bytes."""
        if not vector:
            return b""
        unit = cls.l2_normalize(vector) if normalize else tuple(float(x) for x in vector)
        q_vals: list[int] = []
        for x in unit:
            clamped_x = max(-1.0, min(1.0, x))
            q = round(cls._SCALE * clamped_x)
            q_vals.append(max(-127, min(127, int(q))))
        return struct.pack(f"{len(q_vals)}b", *q_vals)

    @classmethod
    def dequantize(cls, quantized: bytes) -> tuple[float, ...]:
        """Reconstruct approximate float32 components q_i / 127.0 from signed int8 bytes."""
        if not quantized:
            return ()
        view = memoryview(quantized).cast("b")
        return tuple(float(q) / cls._SCALE for q in view)

    @classmethod
    def int8_dot_similarity(cls, q_a: bytes, q_b: bytes) -> float:
        """Compute normalized dot product over packed signed int8 bytes in [-1.0, 1.0]."""
        if not q_a or len(q_a) != len(q_b):
            return 0.0
        mv_a = memoryview(q_a).cast("b")
        mv_b = memoryview(q_b).cast("b")
        dot = sum(a * b for a, b in zip(mv_a, mv_b, strict=True))
        score = float(dot) / (cls._SCALE * cls._SCALE)
        return max(-1.0, min(1.0, score))

    @staticmethod
    def cosine_similarity_float32(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
        """Exact float32 cosine similarity in [-1.0, 1.0]."""
        if not vec_a or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(float(a) * float(b) for a, b in zip(vec_a, vec_b, strict=True))
        norm_a = math.sqrt(sum(float(a) * float(a) for a in vec_a))
        norm_b = math.sqrt(sum(float(b) * float(b) for b in vec_b))
        if norm_a <= 1e-12 or norm_b <= 1e-12:
            return 0.0
        return max(-1.0, min(1.0, dot / (norm_a * norm_b)))

    @classmethod
    def search_with_rescoring(
        cls,
        *,
        query_vector: Sequence[float],
        candidates: Sequence[QuantizedVectorPayload],
        top_k: int,
        oversample_factor: float = 3.0,
    ) -> tuple[QuantizedSearchHit, ...]:
        """Execute Stage 1 INT8 oversampled shortlist followed by exact float32 rescoring."""
        if not candidates or top_k <= 0:
            return ()
        gamma = max(1.0, float(oversample_factor))
        k_oversample = max(top_k, math.ceil(gamma * top_k))
        normalized_query = cls.l2_normalize(query_vector)
        q_query = cls.quantize(normalized_query, normalize=False)

        approx_scored: list[tuple[float, QuantizedVectorPayload]] = [
            (cls.int8_dot_similarity(q_query, payload.quantized_int8), payload)
            for payload in candidates
        ]
        approx_scored.sort(key=lambda item: item[0], reverse=True)
        shortlist = approx_scored[:k_oversample]

        rescored = [
            QuantizedSearchHit(
                point=payload.point,
                quantized_approx_score=approx_score,
                exact_float32_score=cls.cosine_similarity_float32(
                    normalized_query,
                    payload.point.dense_vector,
                ),
            )
            for approx_score, payload in shortlist
        ]
        rescored.sort(key=lambda hit: hit.exact_float32_score, reverse=True)
        return tuple(rescored[:top_k])
