"""Document Intelligence & Semantic Matching Bounded Context exceptions."""

from __future__ import annotations

from talent_screener.domain.shared.exceptions import InvariantViolationError


class BatchQuotaExceededError(InvariantViolationError):
    """Raised when a ScreeningBatch violates file count or size invariants."""

    default_code = "BATCH_QUOTA_EXCEEDED"


class DuplicateFileInBatchError(InvariantViolationError):
    """Raised when a duplicate SHA-256 content hash is added to a ScreeningBatch."""

    default_code = "DUPLICATE_FILE_IN_BATCH"


class BatchCountMismatchError(InvariantViolationError):
    """Raised when batch task counters fail total == completed + failed + pending."""

    default_code = "BATCH_COUNT_INVARIANT_VIOLATION"


class UngroundedClaimError(InvariantViolationError):
    """Raised when an extracted skill or project claim lacks verbatim source_quote grounding."""

    default_code = "UNGROUNDED_SKILL_CLAIM"


class InvalidDateRangeError(InvariantViolationError):
    """Raised when work experience dates produce a negative or inconsistent interval."""

    default_code = "INVALID_DATE_RANGE"


class InvalidMatchWeightsError(InvariantViolationError):
    """Raised when hybrid matching weights do not satisfy alpha + beta + gamma = 1.0."""

    default_code = "INVALID_HYBRID_WEIGHTS"
