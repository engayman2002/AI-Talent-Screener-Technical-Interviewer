"""RFC 9457 Problem Details Exception Translator & Presentation Errors."""

from __future__ import annotations

from collections.abc import Sequence

from talent_screener.application.common.exceptions import (
    ApplicationError,
    ExternalAdapterFailureError,
    GuardrailViolationError,
    IdempotencyConflictError,
    QuotaExceededError,
    ResourceConflictError,
    ResourceNotFoundError,
    UnauthorizedTenantAccessError,
)
from talent_screener.domain.shared.exceptions import (
    ConcurrencyConflictError,
    DomainError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    InvariantViolationError,
    ValidationError,
)
from talent_screener.presentation.exceptions import (
    AuthenticationError,
    FieldViolation,
    MissingRbacScopeError,
    RateLimitExceededError,
    RequestValidationError,
)
from talent_screener.presentation.schemas.envelopes import ProblemDetailsResponse


class ExceptionToProblemDetailsTranslator:
    """Deterministically translates Domain, Application & Transport errors to RFC 9457."""

    _URN_PREFIX = "urn:ai-screener:error"

    def translate(
        self,
        exc: Exception,
        *,
        instance: str,
        trace_id: str,
    ) -> ProblemDetailsResponse:
        """Convert any known exception or unexpected failure into ProblemDetailsResponse."""
        if isinstance(exc, AuthenticationError):
            return self._build(
                status=401,
                title="Unauthorized",
                detail=exc.message,
                error_code=exc.code,
                category="auth",
                instance=instance,
                trace_id=trace_id,
            )

        if isinstance(exc, MissingRbacScopeError):
            return self._build(
                status=403,
                title="Forbidden",
                detail=exc.message,
                error_code=exc.code,
                category="authorization",
                instance=instance,
                trace_id=trace_id,
                metadata={"missing_scopes": exc.missing_scopes},
            )

        if isinstance(exc, UnauthorizedTenantAccessError):
            return self._build(
                status=403,
                title="Forbidden",
                detail=exc.message,
                error_code=exc.code,
                category="tenant_isolation",
                instance=instance,
                trace_id=trace_id,
                metadata=exc.metadata,
            )

        if isinstance(exc, RequestValidationError):
            return self._build(
                status=400,
                title="Bad Request",
                detail=exc.message,
                error_code=exc.code,
                category="validation",
                instance=instance,
                trace_id=trace_id,
                errors=exc.errors,
            )

        if isinstance(exc, RateLimitExceededError):
            return self._build(
                status=429,
                title="Too Many Requests",
                detail=exc.message,
                error_code=exc.code,
                category="rate_limit",
                instance=instance,
                trace_id=trace_id,
                retry_after_seconds=exc.retry_after_seconds,
            )

        if isinstance(exc, ResourceNotFoundError | EntityNotFoundError):
            err_code = exc.code if isinstance(exc, ApplicationError | DomainError) else "NOT_FOUND"
            return self._build(
                status=404,
                title="Not Found",
                detail=str(exc.message),
                error_code=err_code,
                category="resource",
                instance=instance,
                trace_id=trace_id,
            )

        if isinstance(
            exc,
            ResourceConflictError
            | IdempotencyConflictError
            | InvalidStateTransitionError
            | ConcurrencyConflictError,
        ):
            meta = (
                exc.metadata
                if isinstance(exc, ApplicationError)
                else (exc.details if isinstance(exc, DomainError) else {})
            )
            return self._build(
                status=409,
                title="Conflict",
                detail=exc.message,
                error_code=exc.code,
                category="conflict",
                instance=instance,
                trace_id=trace_id,
                metadata=meta,
            )

        if isinstance(exc, GuardrailViolationError):
            return self._build(
                status=422,
                title="Unprocessable Entity",
                detail=exc.message,
                error_code=exc.code,
                category="guardrail",
                instance=instance,
                trace_id=trace_id,
                metadata=exc.metadata,
            )

        if isinstance(exc, QuotaExceededError):
            raw_retry = exc.metadata.get("retry_after_seconds")
            retry_sec = int(raw_retry) if isinstance(raw_retry, int | float) else 5
            return self._build(
                status=429,
                title="Too Many Requests",
                detail=exc.message,
                error_code=exc.code,
                category="quota",
                instance=instance,
                trace_id=trace_id,
                metadata=exc.metadata,
                retry_after_seconds=max(1, retry_sec),
            )

        if isinstance(exc, ValidationError | InvariantViolationError):
            return self._build(
                status=400,
                title="Bad Request",
                detail=exc.message,
                error_code=exc.code,
                category="domain_invariant",
                instance=instance,
                trace_id=trace_id,
                metadata=exc.details,
            )

        if isinstance(exc, ExternalAdapterFailureError):
            if exc.retryable:
                return self._build(
                    status=503,
                    title="Service Unavailable",
                    detail=exc.message,
                    error_code=exc.code,
                    category="upstream_dependency",
                    instance=instance,
                    trace_id=trace_id,
                    metadata=exc.metadata,
                    retry_after_seconds=5,
                )
            return self._build(
                status=502,
                title="Bad Gateway",
                detail=exc.message,
                error_code=exc.code,
                category="upstream_dependency",
                instance=instance,
                trace_id=trace_id,
                metadata=exc.metadata,
            )

        if isinstance(exc, DomainError):
            return self._build(
                status=400,
                title="Domain Error",
                detail=exc.message,
                error_code=exc.code,
                category="domain",
                instance=instance,
                trace_id=trace_id,
                metadata=exc.details,
            )

        if isinstance(exc, ApplicationError):
            return self._build(
                status=500,
                title="Application Failure",
                detail=exc.message,
                error_code=exc.code,
                category="application",
                instance=instance,
                trace_id=trace_id,
                metadata=exc.metadata,
            )

        # Fail closed cleanly without leaking stack traces or internal exception messages
        return self._build(
            status=500,
            title="Internal Server Error",
            detail="An unexpected internal server error occurred.",
            error_code="INTERNAL_SERVER_ERROR",
            category="internal",
            instance=instance,
            trace_id=trace_id,
        )

    def _build(
        self,
        *,
        status: int,
        title: str,
        detail: str,
        error_code: str,
        category: str,
        instance: str,
        trace_id: str,
        errors: Sequence[FieldViolation] = (),
        metadata: object = None,
        retry_after_seconds: int | None = None,
    ) -> ProblemDetailsResponse:
        slug = error_code.lower().replace("_", "-")
        safe_meta = metadata if isinstance(metadata, dict) else (
            dict(metadata.items()) if hasattr(metadata, "items") else {}
        )
        return ProblemDetailsResponse(
            type=f"{self._URN_PREFIX}:{category}:{slug}",
            title=title[:100],
            status=status,
            detail=detail[:500],
            instance=instance,
            error_code=error_code,
            trace_id=trace_id,
            errors=tuple(errors),
            metadata=safe_meta,
            retry_after_seconds=retry_after_seconds,
        )
