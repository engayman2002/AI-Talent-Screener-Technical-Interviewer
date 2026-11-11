"""Recruitment Management Bounded Context exceptions."""

from __future__ import annotations

from talent_screener.domain.shared.exceptions import InvariantViolationError


class RubricWeightSumError(InvariantViolationError):
    """Raised when competency weights fail to sum to 1.0 within 1e-6 tolerance."""

    default_code = "RUBRIC_WEIGHT_SUM_INVALID"


class RubricLockedError(InvariantViolationError):
    """Raised when mutating a rubric locked by active screening or interview sessions."""

    default_code = "RUBRIC_LOCKED_BY_ACTIVE_WORKFLOW"


class GatekeeperRequiredError(InvariantViolationError):
    """Raised when a published requisition lacks at least one Must-Have gatekeeper."""

    default_code = "GATEKEEPER_REQUIRED"
