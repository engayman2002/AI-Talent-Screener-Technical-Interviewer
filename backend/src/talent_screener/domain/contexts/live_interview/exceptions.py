"""Live Interview Bounded Context exceptions."""

from __future__ import annotations

from talent_screener.domain.shared.exceptions import InvariantViolationError


class SocraticProbeLimitExceededError(InvariantViolationError):
    """Raised when consecutive Socratic probes on a competency exceed N_probe <= 2."""

    default_code = "SOCRATIC_PROBE_LIMIT_EXCEEDED"


class InvalidTurnOrderError(InvariantViolationError):
    """Raised when an AdaptiveTurn violates strict monotonic turn numbering."""

    default_code = "NON_MONOTONIC_TURN_ORDER"


class TokenBudgetExceededError(InvariantViolationError):
    """Raised when recording a turn would breach the session FinOps token budget."""

    default_code = "TOKEN_BUDGET_EXCEEDED"


class TurnLimitExceededError(InvariantViolationError):
    """Raised when recording a turn exceeds max_turns configured for the session."""

    default_code = "MAX_TURNS_EXCEEDED"
