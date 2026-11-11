"""BM25 Sparse Lexical Encoder & Dense Embedding Math Engine."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

_TECH_TOKEN_RE = re.compile(
    r"(?:\.[a-z0-9]+|[a-z0-9]+(?:\+\+|#|[+#.-][a-z0-9]+)*)",
    re.I,
)

_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "across",
        "an",
        "and",
        "are",
        "as",
        "at",
        "batch",
        "be",
        "built",
        "by",
        "current",
        "data",
        "depth",
        "developer",
        "engineer",
        "for",
        "from",
        "handling",
        "in",
        "internal",
        "into",
        "is",
        "it",
        "jobs",
        "latency",
        "loss",
        "multi",
        "of",
        "on",
        "or",
        "p99",
        "present",
        "reduced",
        "region",
        "scripts",
        "sec",
        "section",
        "senior",
        "seniority",
        "tenure",
        "that",
        "the",
        "to",
        "using",
        "was",
        "were",
        "with",
        "zero",
        "architected",
        "designed",
        "implemented",
        "led",
        "scaled",
    }
)

# Canonical technical synonym clusters projected into shared dense subspaces.
_SEMANTIC_CLUSTERS: dict[str, str] = {
    "python": "cluster_python",
    "asyncio": "cluster_python",
    "async": "cluster_python",
    "fastapi": "cluster_python_web",
    "uvicorn": "cluster_python_web",
    "distributed": "cluster_distributed",
    "microservices": "cluster_distributed",
    "consensus": "cluster_distributed",
    "raft": "cluster_distributed",
    "partition": "cluster_distributed",
    "sharding": "cluster_distributed",
    "replication": "cluster_distributed",
    "concurrency": "cluster_distributed",
    "kubernetes": "cluster_kubernetes",
    "k8s": "cluster_kubernetes",
    "helm": "cluster_kubernetes",
    "containers": "cluster_kubernetes",
    "docker": "cluster_containers",
    "postgresql": "cluster_sql",
    "postgres": "cluster_sql",
    "sql": "cluster_sql",
    "sqlalchemy": "cluster_sql",
    "rls": "cluster_sql",
    "langchain": "cluster_llm_ai",
    "langgraph": "cluster_llm_ai",
    "llm": "cluster_llm_ai",
    "rag": "cluster_llm_ai",
    "embeddings": "cluster_vector",
    "qdrant": "cluster_vector",
    "pgvector": "cluster_vector",
    "bm25": "cluster_vector",
    "vector": "cluster_vector",
    "kafka": "cluster_streaming",
    "redis": "cluster_cache",
    "typescript": "cluster_frontend",
    "react": "cluster_frontend",
    "next.js": "cluster_frontend",
}

SparseVector = tuple[tuple[str, float], ...]


def tokenize_technical_text(text: str, *, include_clusters: bool = False) -> tuple[str, ...]:
    """Tokenize technical prose while preserving symbols (C++, C#, .NET, Next.js)."""
    raw_matches = _TECH_TOKEN_RE.findall(text.lower())
    tokens: list[str] = []
    for tok in raw_matches:
        cleaned = tok.strip("._-")
        if not cleaned or cleaned in _STOP_WORDS:
            continue
        # Filter out non-semantic numeric/header/date expressions and PII tokens.
        if (
            cleaned.isdigit()
            or cleaned.startswith("[pii_")
            or cleaned.startswith("pii_")
            or re.fullmatch(r"\d+[a-z%]*", cleaned)
            or re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", cleaned)
        ):
            continue
        if cleaned.startswith("skill_"):
            sub_parts = [p for p in cleaned.removeprefix("skill_").split("_") if p and p != "lang"]
            for sub in sub_parts:
                if sub not in _STOP_WORDS:
                    tokens.append(sub)
                    if include_clusters and sub in _SEMANTIC_CLUSTERS:
                        tokens.append(_SEMANTIC_CLUSTERS[sub])
            continue
        tokens.append(cleaned)
        # Decompose hyphen/dot compounds so "next.js" also surfaces "next" while keeping exact term.
        if "." in cleaned or "-" in cleaned:
            for part in re.split(r"[.-]", cleaned):
                if len(part) >= 2 and part not in _STOP_WORDS:
                    tokens.append(part)
        if include_clusters and cleaned in _SEMANTIC_CLUSTERS:
            tokens.append(_SEMANTIC_CLUSTERS[cleaned])
    return tuple(tokens)


@runtime_checkable
class DenseEmbeddingClientProtocol(Protocol):
    """Protocol compatible with LangChain Embeddings (sync embed_documents / embed_query)."""

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...


class DenseVectorMath:
    """Deterministic L2 normalization and bounded [0, 1] cosine similarity helper."""

    @staticmethod
    def l2_normalize(vector: Sequence[float]) -> tuple[float, ...]:
        """Return unit L2-normalized vector (or zero vector if norm is 0)."""
        sq_sum = sum(float(v) * float(v) for v in vector)
        if sq_sum <= 0.0:
            return tuple(0.0 for _ in vector)
        norm = math.sqrt(sq_sum)
        return tuple(float(v) / norm for v in vector)

    @staticmethod
    def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
        """Compute cosine similarity clamped to [0.0, 1.0]."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for a_val, b_val in zip(vec_a, vec_b, strict=True):
            fa = float(a_val)
            fb = float(b_val)
            dot += fa * fb
            norm_a += fa * fa
            norm_b += fb * fb
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        raw = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
        return max(0.0, min(1.0, raw))


class HashProjectionDenseEmbeddingClient:
    """Deterministic local DenseEmbeddingClientProtocol via semantic feature hashing."""

    def __init__(self, *, dimension: int = 256) -> None:
        if dimension < 32:
            raise ValueError("Embedding dimension must be >= 32.")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def _project_single(self, text: str) -> tuple[float, ...]:
        tokens = tokenize_technical_text(text, include_clusters=True)
        if not tokens:
            return tuple(0.0 for _ in range(self._dimension))
        vec = [0.0] * self._dimension
        counts = Counter(tokens)
        for token, count in counts.items():
            # Core semantic concept clusters dominate technical subspace alignment.
            weight = (1.0 + math.log1p(count)) * (
                4.0 if token.startswith("cluster_") else 0.75
            )
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx_a = int.from_bytes(digest[0:4], "big") % self._dimension
            sign_a = 1.0 if (digest[4] & 1) == 0 else -1.0
            idx_b = int.from_bytes(digest[8:12], "big") % self._dimension
            sign_b = 1.0 if (digest[12] & 1) == 0 else -1.0
            vec[idx_a] += sign_a * weight
            vec[idx_b] += sign_b * (0.5 * weight)
        return DenseVectorMath.l2_normalize(vec)

    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """Batch-embed document passages deterministically into L2 unit vectors."""
        return [self._project_single(text) for text in texts]

    def embed_query(self, text: str) -> tuple[float, ...]:
        """Embed a single competency anchor query into an L2 unit vector."""
        return self._project_single(text)


class Bm25SparseEncoder:
    """Pure deterministic BM25 sparse lexical encoder with [0, 1] query normalization."""

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 <= 0.0:
            raise ValueError("BM25 parameter k1 must be > 0.")
        if not 0.0 <= b <= 1.0:
            raise ValueError("BM25 parameter b must be in [0.0, 1.0].")
        self.k1 = k1
        self.b = b

    def encode_corpus(
        self,
        documents: Sequence[str],
    ) -> tuple[list[SparseVector], dict[str, float]]:
        """Compute per-document BM25 sparse term weights and corpus IDF lookup."""
        tokenized_docs = [tokenize_technical_text(doc) for doc in documents]
        num_docs = len(tokenized_docs)
        if num_docs == 0:
            return [], {}

        avgdl = sum(len(toks) for toks in tokenized_docs) / max(1, num_docs)
        avgdl = max(1.0, avgdl)

        df_counts: Counter[str] = Counter()
        for toks in tokenized_docs:
            df_counts.update(set(toks))

        idf_map: dict[str, float] = {
            term: math.log(1.0 + (num_docs - df + 0.5) / (df + 0.5))
            for term, df in df_counts.items()
        }

        sparse_vectors: list[SparseVector] = []
        for toks in tokenized_docs:
            if not toks:
                sparse_vectors.append(())
                continue
            tf_counts = Counter(toks)
            doc_len = len(toks)
            norm_len = 1.0 - self.b + self.b * (doc_len / avgdl)
            weights: list[tuple[str, float]] = []
            for term in sorted(tf_counts):
                tf = tf_counts[term]
                idf = idf_map[term]
                score = idf * (tf * (self.k1 + 1.0)) / (tf + self.k1 * norm_len)
                weights.append((term, score))
            sparse_vectors.append(tuple(weights))
        return sparse_vectors, idf_map

    def score_query(
        self,
        *,
        query_text: str,
        doc_sparse_vector: SparseVector,
        idf_map: Mapping[str, float],
        corpus_doc_count: int = 1,
    ) -> float:
        """Compute normalized BM25 score in [0.0, 1.0] for one query against one chunk."""
        query_tokens = tokenize_technical_text(query_text)
        if not query_tokens or not doc_sparse_vector:
            return 0.0

        unique_q = list(dict.fromkeys(query_tokens))
        doc_map = dict(doc_sparse_vector)
        default_idf = math.log(1.0 + (max(1, corpus_doc_count) + 0.5) / 1.5)

        raw_score = 0.0
        max_possible = 0.0
        for q_tok in unique_q:
            q_idf = idf_map.get(q_tok, default_idf)
            max_possible += q_idf
            raw_score += doc_map.get(q_tok, 0.0)

        if max_possible <= 0.0 or raw_score <= 0.0:
            return 0.0
        return max(0.0, min(1.0, raw_score / max_possible))
