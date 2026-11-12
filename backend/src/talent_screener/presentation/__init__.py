"""Clean Architecture Presentation Delivery Layer (REST, SSE, Middleware & WebSocket)."""

from talent_screener.presentation.middleware import (
    BearerTokenAuthenticator,
    ExceptionToProblemDetailsTranslator,
    IdempotencyMiddleware,
    PrincipalContext,
    RbacScope,
    TenantRateLimiter,
)
from talent_screener.presentation.openapi import build_openapi_spec, render_swagger_ui_html
from talent_screener.presentation.rest import RestApiRouter
from talent_screener.presentation.schemas import (
    ApiResponseEnvelope,
    HttpRequest,
    HttpResponse,
    ProblemDetailsResponse,
)
from talent_screener.presentation.server import (
    AsgiAppFn,
    AsyncStdlibHttpWsServer,
    create_asgi_app,
)
from talent_screener.presentation.websocket import WebSocketInterviewGateway

__all__ = [
    "ApiResponseEnvelope",
    "AsgiAppFn",
    "AsyncStdlibHttpWsServer",
    "BearerTokenAuthenticator",
    "ExceptionToProblemDetailsTranslator",
    "HttpRequest",
    "HttpResponse",
    "IdempotencyMiddleware",
    "PrincipalContext",
    "ProblemDetailsResponse",
    "RbacScope",
    "RestApiRouter",
    "TenantRateLimiter",
    "WebSocketInterviewGateway",
    "build_openapi_spec",
    "create_asgi_app",
    "render_swagger_ui_html",
]
