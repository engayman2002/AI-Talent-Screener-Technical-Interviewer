"""Canonical Presentation Transport Exceptions & FieldViolation primitives."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldViolation:
    """Structured field-level constraint violation attached to RFC 9457 errors."""

    field_path: str
    constraint: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "field_path": self.field_path,
            "constraint": self.constraint,
            "reason": self.reason,
        }


class PresentationError(Exception):
    """Base error raised at the Presentation API/WebSocket transport boundary."""

    default_code: str = "PRESENTATION_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class AuthenticationError(PresentationError):
    """Raised when Bearer JWT or ticket credentials are missing, malformed, or expired."""

    default_code = "AUTHENTICATION_FAILED"


class MissingRbacScopeError(PresentationError):
    """Raised when an authenticated principal lacks required RBAC permission scopes."""

    default_code = "INSUFFICIENT_RBAC_SCOPE"

    def __init__(
        self,
        message: str,
        *,
        missing_scopes: Sequence[str] = (),
        code: str | None = None,
    ) -> None:
        super().__init__(message, code=code or self.default_code)
        self.missing_scopes = tuple(missing_scopes)


class RequestValidationError(PresentationError):
    """Raised when incoming wire-format JSON, headers, or path parameters are invalid."""

    default_code = "REQUEST_VALIDATION_FAILED"

    def __init__(
        self,
        message: str,
        *,
        errors: Sequence[FieldViolation] = (),
        code: str | None = None,
    ) -> None:
        super().__init__(message, code=code or self.default_code)
        self.errors = tuple(errors)


class RateLimitExceededError(PresentationError):
    """Raised when a tenant exceeds its HTTP endpoint rate-limit allowance."""

    default_code = "RATE_LIMIT_EXCEEDED"

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int = 1,
        code: str | None = None,
    ) -> None:
        super().__init__(message, code=code or self.default_code)
        self.retry_after_seconds = max(1, retry_after_seconds)
