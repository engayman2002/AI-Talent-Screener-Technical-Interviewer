"""OpenAPI 3.1.0 Specification Builder and Standalone Swagger UI Renderer."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

from talent_screener.presentation.rest.router import RouteSpec

_PATH_PARAM_RE = re.compile(r"\(\?P<([a-zA-Z_][a-zA-Z0-9_]*)>[^)]+\)")


def _regex_to_openapi_path(pattern: re.Pattern[str]) -> tuple[str, list[str]]:
    raw = pattern.pattern.lstrip("^").rstrip("$")
    params: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        params.append(name)
        return f"{{{name}}}"

    openapi_path = _PATH_PARAM_RE.sub(_replace, raw)
    return openapi_path, params


def _tag_for_path(path: str) -> str:
    if (
        path.startswith("/v1/requisitions")
        and "comparison-matrix" not in path
        and "/match" not in path
    ):
        return "Recruitment Management"
    if (
        path.startswith("/v1/screening-batches")
        or path.endswith("/match")
    ):
        return "Document Intelligence & Matching"
    if path.startswith("/v1/interviews"):
        return "Live Adaptive Interview"
    if path.startswith("/v1/evaluations") or "comparison-matrix" in path:
        return "Evaluation & Analytics"
    if (
        path.startswith("/v1/tenants")
        or path.startswith("/v1/consents")
        or path.startswith("/v1/candidates")
    ):
        return "Identity & GDPR Governance"
    return "Operations & Interactive Demo"


def build_openapi_spec(
    routes: Sequence[RouteSpec],
    *,
    title: str = "AI Talent Screener & Technical Interviewer API",
    version: str = "1.0.0",
    description: str = (
        "Clean Architecture DDD platform with PII cryptographic vault, INT8 hybrid vector "
        "matching, LangGraph 4-plane adaptive interviewing, RFC 9457 Problem Details, "
        "SSE batch telemetry, and full-duplex WebSocket interview protocol."
    ),
) -> dict[str, object]:
    """Compile a standards-compliant OpenAPI 3.1.0 specification from registered RouteSpecs."""
    paths: dict[str, dict[str, object]] = {}

    for spec in routes:
        oa_path, param_names = _regex_to_openapi_path(spec.pattern)
        method_key = spec.method.lower()
        path_item = paths.setdefault(oa_path, {})

        parameters = [
            {
                "name": pname,
                "in": "path",
                "required": True,
                "schema": {
                    "type": "string",
                    "format": "uuid" if pname.endswith("_id") else "string",
                },
            }
            for pname in param_names
        ]

        op_slug = (
            oa_path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
            or "root"
        )
        operation: dict[str, object] = {
            "tags": [_tag_for_path(oa_path)],
            "operationId": f"{method_key}_{op_slug}",
            "summary": f"{spec.method} {oa_path}",
            "responses": {
                "200": {
                    "description": "Success response wrapped in canonical ApiResponseEnvelope",
                    "content": {
                        "text/event-stream"
                        if oa_path.endswith("/events")
                        else "application/json": {
                            "schema": {"$ref": "#/components/schemas/ApiResponseEnvelope"}
                        }
                    },
                },
                "400": {
                    "description": "Validation or domain rule failure (RFC 9457)",
                    "content": {
                        "application/problem+json": {
                            "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                        }
                    },
                },
            },
        }
        if parameters:
            operation["parameters"] = parameters
        if spec.requires_auth:
            operation["security"] = [{"bearerAuth": list(spec.required_scopes)}]
        if spec.method in {"POST", "PUT", "PATCH"} and "simulate" not in oa_path:
            operation["requestBody"] = {
                "required": False,
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "additionalProperties": True}
                    }
                },
            }

        path_item[method_key] = operation

    # Document the full-duplex WebSocket endpoint as an extension reference path
    paths["/v1/interviews/{session_id}/ws"] = {
        "get": {
            "tags": ["Live Adaptive Interview"],
            "summary": "Full-Duplex Stateful Interview WebSocket Upgrade",
            "description": (
                "RFC 6455 WebSocket endpoint accepting ?ticket=<JWT> and optional "
                "?last_server_seq=<int>. Emits session.ready, interviewer.question, "
                "turn.evaluation_ack, session.completed, and tts.audio_chunk frames."
            ),
            "parameters": [
                {
                    "name": "session_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                },
                {
                    "name": "ticket",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                },
            ],
            "responses": {
                "101": {"description": "Switching Protocols (WebSocket upgraded)"}
            },
        }
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": version,
            "description": description,
        },
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
            "schemas": {
                "ApiResponseEnvelope": {
                    "type": "object",
                    "required": ["data", "meta"],
                    "properties": {
                        "data": {"type": "object", "additionalProperties": True},
                        "meta": {
                            "type": "object",
                            "properties": {
                                "request_id": {"type": "string"},
                                "trace_id": {"type": "string"},
                                "tenant_id": {"type": ["string", "null"]},
                                "api_version": {"type": "string"},
                                "timestamp": {"type": "string", "format": "date-time"},
                            },
                        },
                    },
                },
                "ProblemDetails": {
                    "type": "object",
                    "required": ["type", "title", "status", "detail", "error_code", "trace_id"],
                    "properties": {
                        "type": {"type": "string"},
                        "title": {"type": "string"},
                        "status": {"type": "integer"},
                        "detail": {"type": "string"},
                        "instance": {"type": "string"},
                        "error_code": {"type": "string"},
                        "trace_id": {"type": "string"},
                        "timestamp": {"type": "string", "format": "date-time"},
                        "errors": {"type": "array", "items": {"type": "object"}},
                    },
                },
            },
        },
    }


def render_swagger_ui_html(
    *,
    openapi_url: str = "/openapi.json",
    title: str = "AI Talent Screener API Docs",
) -> str:
    """Render standalone Swagger UI documentation page consuming /openapi.json."""
    safe_title = html.escape(title, quote=True)
    safe_spec_url = html.escape(openapi_url, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_title}</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
  <style>
    body {{ margin: 0; background: #0f172a; color: #e2e8f0; font-family: system-ui, sans-serif; }}
    .topbar-banner {{
      padding: 14px 24px;
      background: #020617;
      border-bottom: 1px solid #1e293b;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .topbar-banner a {{ color: #38bdf8; text-decoration: none; font-size: 13px; }}
    #swagger-ui {{ background: #ffffff; }}
  </style>
</head>
<body>
  <div class="topbar-banner">
    <strong>{safe_title} — OpenAPI 3.1 Explorer</strong>
    <div>
      <a href="{safe_spec_url}">Download openapi.json</a> &nbsp;|&nbsp;
      <a href="/v1/ops/demo-manifest">View Demo Seed Manifest</a>
    </div>
  </div>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.addEventListener("load", () => {{
      if (window.SwaggerUIBundle) {{
        window.SwaggerUIBundle({{
          url: "{safe_spec_url}",
          dom_id: "#swagger-ui",
          deepLinking: true
        }});
      }}
    }});
  </script>
</body>
</html>
"""


__all__ = ["build_openapi_spec", "render_swagger_ui_html"]
