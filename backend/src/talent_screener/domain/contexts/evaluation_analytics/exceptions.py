"""Evaluation & Analytics Bounded Context exceptions."""

from __future__ import annotations

from talent_screener.domain.shared.exceptions import InvariantViolationError


class ReportImmutableError(InvariantViolationError):
    """Raised when attempting to mutate a finalized EvaluationReport directly."""

    default_code = "REPORT_IMMUTABLE"


class MissingEvidenceTurnError(InvariantViolationError):
    """Raised when a CompetencyScorecardItem lacks verifiable AdaptiveTurn references."""

    default_code = "MISSING_TURN_EVIDENCE"


class CompositeScoreMismatchError(InvariantViolationError):
    """Raised when CompositeRubricScore diverges from weighted scorecard items."""

    default_code = "COMPOSITE_SCORE_MISMATCH"
