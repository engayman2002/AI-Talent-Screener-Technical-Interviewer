"""Presentation Layer Security, Error Translation, Rate-Limit & Idempotency Middleware."""

from talent_screener.presentation.exceptions import (
    AuthenticationError,
    MissingRbacScopeError,
    PresentationError,
    RateLimitExceededError,
    RequestValidationError,
)
from talent_screener.presentation.middleware.auth import (
    BearerTokenAuthenticator,
    PrincipalContext,
    RbacScope,
)
from talent_screener.presentation.middleware.error_handler import (
    ExceptionToProblemDetailsTranslator,
)
from talent_screener.presentation.middleware.idempotency import IdempotencyMiddleware
from talent_screener.presentation.middleware.rate_limit import (
    RateLimitHeaders,
    TenantRateLimiter,
)

__all__ = [
    "AuthenticationError",
    "BearerTokenAuthenticator",
    "ExceptionToProblemDetailsTranslator",
    "IdempotencyMiddleware",
    "MissingRbacScopeError",
    "PresentationError",
    "PrincipalContext",
    "RateLimitExceededError",
    "RateLimitHeaders",
    "RbacScope",
    "RequestValidationError",
    "TenantRateLimiter",
]
