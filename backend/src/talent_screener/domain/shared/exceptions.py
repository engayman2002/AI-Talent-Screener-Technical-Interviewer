"""Domain-level exception hierarchy with canonical machine-readable error codes."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


class DomainError(Exception):
    """Base exception for all tactical DDD invariant and domain rule violations."""

    default_code: str = "DOMAIN_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.code: str = code or self.default_code
        self.details: Mapping[str, object] = (
            MappingProxyType(dict(details)) if details is not None else MappingProxyType({})
        )

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class InvariantViolationError(DomainError):
    """Raised when an entity, value object, or aggregate business invariant is breached."""

    default_code = "INVARIANT_VIOLATION"


class ValidationError(InvariantViolationError):
    """Raised when structural or scalar validation rules fail."""

    default_code = "VALIDATION_ERROR"


class InvalidStateTransitionError(DomainError):
    """Raised when an aggregate lifecycle state transition is disallowed."""

    default_code = "INVALID_STATE_TRANSITION"

    def __init__(
        self,
        message: str,
        *,
        from_state: str | None = None,
        to_state: str | None = None,
        code: str | None = None,
    ) -> None:
        details: dict[str, object] = {}
        if from_state is not None:
            details["from_state"] = from_state
        if to_state is not None:
            details["to_state"] = to_state
        super().__init__(message, code=code or self.default_code, details=details)
        self.from_state = from_state
        self.to_state = to_state


class ConcurrencyConflictError(DomainError):
    """Raised on optimistic concurrency version mismatch."""

    default_code = "CONCURRENCY_CONFLICT"


class EntityNotFoundError(DomainError):
    """Raised when an expected entity or child record cannot be located."""

    default_code = "ENTITY_NOT_FOUND"
