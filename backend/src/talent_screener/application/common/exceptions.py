"""Application-layer exception hierarchy with canonical machine-readable error codes."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


class ApplicationError(Exception):
    """Base exception for application orchestration and port contract failures."""

    default_code: str = "APPLICATION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.code: str = code or self.default_code
        self.metadata: Mapping[str, object] = (
            MappingProxyType(dict(metadata)) if metadata is not None else MappingProxyType({})
        )

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ResourceNotFoundError(ApplicationError):
    """Raised when a requested aggregate or read projection does not exist."""

    default_code = "RESOURCE_NOT_FOUND"


class ResourceConflictError(ApplicationError):
    """Raised when an operation conflicts with existing aggregate or uniqueness state."""

    default_code = "RESOURCE_CONFLICT"


class IdempotencyConflictError(ApplicationError):
    """Raised when an idempotency key is reused with mismatched payload or active lock."""

    default_code = "IDEMPOTENCY_CONFLICT"


class UnauthorizedTenantAccessError(ApplicationError):
    """Raised on tenant boundary mismatch or suspended-tenant access violation."""

    default_code = "UNAUTHORIZED_TENANT_ACCESS"


class QuotaExceededError(ApplicationError):
    """Raised when tenant batch, concurrency, or interview quota ceilings are exceeded."""

    default_code = "QUOTA_EXCEEDED"


class ExternalAdapterFailureError(ApplicationError):
    """Raised when an infrastructure outbound adapter fails across an ACL boundary."""

    default_code = "EXTERNAL_ADAPTER_FAILURE"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = True,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, metadata=metadata)
        self.retryable: bool = retryable


class GuardrailViolationError(ApplicationError):
    """Raised when an adversarial prompt injection or safety guardrail triggers a hard block."""

    default_code = "GUARDRAIL_VIOLATION"
