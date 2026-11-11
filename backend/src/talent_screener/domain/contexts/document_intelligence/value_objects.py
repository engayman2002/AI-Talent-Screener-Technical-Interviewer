"""Document Intelligence Bounded Context Value Objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.exceptions import (
    UngroundedClaimError,
)
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    require_finite_float,
    require_int_range,
    require_non_empty_str,
    require_non_nil_uuid,
)
from talent_screener.domain.shared.value_objects import (
    ConfidenceScore,
    SHA256Hash,
    ValueObject,
)

MAX_CV_FILE_SIZE_BYTES: int = 15 * 1024 * 1024  # 15 MB per Phase 1 / Phase 5 spec
ALLOWED_CV_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)


class BatchStatus(StrEnum):
    """Lifecycle states of a ScreeningBatch aggregate."""

    INITIATED = "INITIATED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class TaskIngestionStatus(StrEnum):
    """Per-document processing states inside a ScreeningBatch."""

    PENDING = "PENDING"
    SCANNING = "SCANNING"
    PARSING = "PARSING"
    REDACTING = "REDACTING"
    EXTRACTING = "EXTRACTING"
    INDEXING = "INDEXING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"
    DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"


@dataclass(frozen=True, slots=True)
class FileContentHash(ValueObject):
    """Content-addressable fingerprint and container metadata for an uploaded CV."""

    sha256: SHA256Hash
    file_size_bytes: int
    mime_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.sha256, SHA256Hash):
            raise InvariantViolationError(
                "sha256 must be a SHA256Hash value object.",
                code="INVALID_SHA256_HASH",
            )
        require_int_range(
            self.file_size_bytes,
            "file_size_bytes",
            1,
            MAX_CV_FILE_SIZE_BYTES,
            code="INVALID_FILE_SIZE",
        )
        cleaned_mime = require_non_empty_str(
            self.mime_type,
            "mime_type",
            max_len=128,
            code="UNSUPPORTED_MIME_TYPE",
        ).lower()
        if cleaned_mime not in ALLOWED_CV_MIME_TYPES:
            raise InvariantViolationError(
                f"Unsupported CV MIME type: {cleaned_mime!r}.",
                code="UNSUPPORTED_MIME_TYPE",
            )
        object.__setattr__(self, "mime_type", cleaned_mime)


@dataclass(frozen=True, slots=True)
class ExtractedSkillClaim(ValueObject):
    """Normalized candidate skill claim grounded by an explicit verbatim source_quote."""

    canonical_skill_id: str
    raw_label: str
    source_quote: str
    claimed_depth: int = 1
    applied_months: int = 0
    recency_months: int = 0
    confidence: ConfidenceScore = field(default_factory=lambda: ConfidenceScore(1.0))

    def __post_init__(self) -> None:
        cleaned_skill = require_non_empty_str(
            self.canonical_skill_id,
            "canonical_skill_id",
            max_len=120,
            code="INVALID_SKILL_ID",
        )
        cleaned_label = require_non_empty_str(
            self.raw_label,
            "raw_label",
            max_len=120,
            code="INVALID_SKILL_LABEL",
        )
        if not isinstance(self.source_quote, str) or len(self.source_quote.strip()) < 3:
            raise UngroundedClaimError(
                "ExtractedSkillClaim requires a non-empty verbatim source_quote (>= 3 chars).",
            )
        cleaned_quote = self.source_quote.strip()
        if len(cleaned_quote) > 500:
            raise UngroundedClaimError(
                "ExtractedSkillClaim source_quote must not exceed 500 characters.",
                code="SOURCE_QUOTE_TOO_LONG",
            )
        require_int_range(self.claimed_depth, "claimed_depth", 1, 5, code="INVALID_CLAIMED_DEPTH")
        require_int_range(
            self.applied_months, "applied_months", 0, 720, code="INVALID_APPLIED_MONTHS"
        )
        require_int_range(
            self.recency_months, "recency_months", 0, 720, code="INVALID_RECENCY_MONTHS"
        )
        if not isinstance(self.confidence, ConfidenceScore):
            raise InvariantViolationError(
                "confidence must be a ConfidenceScore.",
                code="INVALID_CONFIDENCE_SCORE",
            )
        object.__setattr__(self, "canonical_skill_id", cleaned_skill)
        object.__setattr__(self, "raw_label", cleaned_label)
        object.__setattr__(self, "source_quote", cleaned_quote)


@dataclass(frozen=True, slots=True)
class SeniorityEstimate(ValueObject):
    """Estimated career seniority band derived deterministically from verified tenure."""

    level: str
    verified_experience_months: int
    confidence: ConfidenceScore

    _VALID_LEVELS = frozenset(
        {"INTERN", "JUNIOR", "MID", "SENIOR", "STAFF", "PRINCIPAL", "MANAGER_DIRECTOR"}
    )

    def __post_init__(self) -> None:
        cleaned = require_non_empty_str(
            self.level,
            "seniority_level",
            max_len=40,
            code="INVALID_SENIORITY_LEVEL",
        ).upper()
        if cleaned not in self._VALID_LEVELS:
            raise InvariantViolationError(
                f"Unsupported seniority estimate level: {cleaned!r}.",
                code="INVALID_SENIORITY_LEVEL",
            )
        require_int_range(
            self.verified_experience_months,
            "verified_experience_months",
            0,
            720,
            code="INVALID_EXPERIENCE_MONTHS",
        )
        if not isinstance(self.confidence, ConfidenceScore):
            raise InvariantViolationError(
                "confidence must be a ConfidenceScore.",
                code="INVALID_CONFIDENCE_SCORE",
            )
        object.__setattr__(self, "level", cleaned)


@dataclass(frozen=True, slots=True)
class ImpactMetric(ValueObject):
    """Quantified engineering impact metric grounded in a verbatim CV quote."""

    metric_type: str
    numeric_value: float
    unit: str
    source_quote: str

    def __post_init__(self) -> None:
        cleaned_type = require_non_empty_str(
            self.metric_type,
            "metric_type",
            max_len=64,
            code="INVALID_IMPACT_METRIC",
        ).upper()
        val = require_finite_float(
            self.numeric_value, "numeric_value", code="INVALID_IMPACT_METRIC"
        )
        cleaned_unit = require_non_empty_str(
            self.unit,
            "unit",
            max_len=32,
            code="INVALID_IMPACT_METRIC",
        )
        if not isinstance(self.source_quote, str) or len(self.source_quote.strip()) < 3:
            raise UngroundedClaimError(
                "ImpactMetric requires a non-empty verbatim source_quote.",
            )
        object.__setattr__(self, "metric_type", cleaned_type)
        object.__setattr__(self, "numeric_value", val)
        object.__setattr__(self, "unit", cleaned_unit)
        object.__setattr__(self, "source_quote", self.source_quote.strip())


@dataclass(frozen=True, slots=True)
class PiiRedactionMap(ValueObject):
    """Zero-PII audit map linking surrogate tokens to redacted entity categories."""

    vault_reference_id: UUID
    token_categories: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        require_non_nil_uuid(
            self.vault_reference_id,
            "vault_reference_id",
            code="INVALID_PII_VAULT_REF",
        )
        seen_tokens: set[str] = set()
        normalized: list[tuple[str, str]] = []
        for raw_token, raw_category in self.token_categories:
            token = require_non_empty_str(
                raw_token,
                "surrogate_token",
                max_len=128,
                code="INVALID_PII_TOKEN",
            )
            category = require_non_empty_str(
                raw_category,
                "pii_category",
                max_len=64,
                code="INVALID_PII_CATEGORY",
            ).upper()
            if token in seen_tokens:
                raise InvariantViolationError(
                    f"Duplicate surrogate token {token!r} in PiiRedactionMap.",
                    code="DUPLICATE_PII_TOKEN",
                )
            seen_tokens.add(token)
            normalized.append((token, category))
        object.__setattr__(self, "token_categories", tuple(normalized))

    @classmethod
    def from_mapping(
        cls,
        *,
        vault_reference_id: UUID,
        token_categories: Mapping[str, str],
    ) -> PiiRedactionMap:
        """Create an immutable PiiRedactionMap from a token -> category mapping."""
        return cls(
            vault_reference_id=vault_reference_id,
            token_categories=tuple(token_categories.items()),
        )

    @property
    def redacted_entity_count(self) -> int:
        return len(self.token_categories)

    def as_mapping(self) -> Mapping[str, str]:
        """Return a read-only view of token -> PII category."""
        return MappingProxyType(dict(self.token_categories))
