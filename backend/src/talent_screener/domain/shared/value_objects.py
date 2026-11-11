"""Shared Kernel Value Objects and ValueObject base abstraction."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from uuid import UUID

from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    require_float_range,
    require_int_range,
    require_non_nil_uuid,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ValueObject:
    """Base abstraction for strictly immutable, structurally compared Value Objects."""


@dataclass(frozen=True, slots=True)
class TenantId(ValueObject):
    """Multi-tenant isolation identifier shared across all Bounded Contexts."""

    value: UUID

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.value, "tenant_id", code="INVALID_TENANT_ID")

    @classmethod
    def generate(cls) -> TenantId:
        """Generate a new time-ordered TenantId."""
        return cls(generate_uuid7())

    @classmethod
    def from_str(cls, raw: str) -> TenantId:
        """Parse a UUID string into a TenantId."""
        try:
            parsed = UUID(raw)
        except (ValueError, TypeError) as exc:
            raise InvariantViolationError(
                f"Invalid TenantId string: {raw!r}.",
                code="INVALID_TENANT_ID",
            ) from exc
        return cls(parsed)

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class SHA256Hash(ValueObject):
    """Canonical lowercase 64-hex-character SHA-256 cryptographic digest."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvariantViolationError(
                "SHA256Hash value must be a string.",
                code="INVALID_SHA256_HASH",
            )
        normalized = self.value.strip().lower()
        if not _SHA256_HEX_RE.fullmatch(normalized):
            raise InvariantViolationError(
                "SHA256Hash must be a valid 64-character hexadecimal digest.",
                code="INVALID_SHA256_HASH",
            )
        object.__setattr__(self, "value", normalized)

    @classmethod
    def from_bytes(cls, payload: bytes) -> SHA256Hash:
        """Compute a SHA256Hash from raw bytes."""
        return cls(hashlib.sha256(payload).hexdigest())

    @classmethod
    def from_text(cls, text: str) -> SHA256Hash:
        """Compute a SHA256Hash from UTF-8 encoded text."""
        return cls.from_bytes(text.encode("utf-8"))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class VersionNumber(ValueObject):
    """Strictly positive monotonic integer version (>= 1)."""

    value: int

    def __post_init__(self) -> None:
        require_int_range(self.value, "version_number", 1, code="INVALID_VERSION_NUMBER")

    def next(self) -> VersionNumber:
        """Return the subsequent incremented version."""
        return VersionNumber(self.value + 1)

    def __int__(self) -> int:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class ConfidenceScore(ValueObject):
    """Probability / epistemic confidence bounded strictly in [0.0, 1.0]."""

    value: float

    def __post_init__(self) -> None:
        numeric = require_float_range(
            self.value,
            "confidence_score",
            0.0,
            1.0,
            code="INVALID_CONFIDENCE_SCORE",
        )
        object.__setattr__(self, "value", numeric)

    def __float__(self) -> float:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class NormalizedScore(ValueObject):
    """Unit-normalized score strictly bounded in [0.0, 1.0]."""

    value: float

    def __post_init__(self) -> None:
        numeric = require_float_range(
            self.value,
            "normalized_score",
            0.0,
            1.0,
            code="INVALID_NORMALIZED_SCORE",
        )
        object.__setattr__(self, "value", numeric)

    @classmethod
    def from_percentage(cls, percentage: float) -> NormalizedScore:
        """Convert a [0.0, 100.0] score into a unit-normalized score."""
        pct = require_float_range(
            percentage,
            "percentage",
            0.0,
            100.0,
            code="INVALID_PERCENTAGE_SCORE",
        )
        return cls(pct / 100.0)

    def to_percentage(self) -> float:
        """Project the normalized score onto a [0.0, 100.0] scale."""
        return self.value * 100.0

    def __float__(self) -> float:
        return self.value
