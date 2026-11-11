"""Canonical REST API Response Envelopes, RFC 9457 Problem Details & Transport Models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from uuid import UUID

from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.presentation.exceptions import FieldViolation


def serialize_value(value: object) -> object:
    """Recursively convert Domain/Application objects and DTOs into JSON-safe primitives."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, TenantId):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {k: serialize_value(v) for k, v in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): serialize_value(v) for k, v in value.items()}
    if isinstance(value, Sequence | set | frozenset):
        return [serialize_value(item) for item in value]
    return str(value)


@dataclass(frozen=True, slots=True)
class ProblemDetailsResponse:
    """Canonical RFC 9457 Problem Details error payload."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    error_code: str
    trace_id: str
    timestamp: datetime = field(default_factory=utc_now)
    errors: tuple[FieldViolation, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    retry_after_seconds: int | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": self.instance,
            "error_code": self.error_code,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp.isoformat(),
            "errors": [item.to_dict() for item in self.errors],
            "metadata": serialize_value(self.metadata),
        }
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        return payload


@dataclass(frozen=True, slots=True)
class ResponseMeta:
    """Traceability and API version metadata embedded in every success envelope."""

    request_id: str
    trace_id: str
    tenant_id: TenantId | None = None
    api_version: str = "v1"
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "tenant_id": str(self.tenant_id.value) if self.tenant_id is not None else None,
            "api_version": self.api_version,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ApiResponseEnvelope[T]:
    """Typed standard REST response wrapper carrying data and operational metadata."""

    data: T
    meta: ResponseMeta

    def to_dict(self) -> dict[str, object]:
        return {
            "data": serialize_value(self.data),
            "meta": self.meta.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """Framework-agnostic HTTP request envelope dispatched by RestApiRouter."""

    method: str
    path: str
    headers: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    query_params: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    body: Mapping[str, object] | None = None

    def header(self, name: str, default: str | None = None) -> str | None:
        """Lookup HTTP header case-insensitively."""
        target = name.lower()
        for key, value in self.headers.items():
            if key.lower() == target:
                return value
        return default


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Framework-agnostic HTTP response emitted by REST controllers and router."""

    status_code: int
    body: Mapping[str, object] | None = None
    headers: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    raw_text: str | None = None
    content_type: str = "application/json"

    def with_headers(self, extra_headers: Mapping[str, str]) -> HttpResponse:
        """Return a copy of this response with additional transport headers merged."""
        merged = dict(self.headers)
        merged.update(extra_headers)
        return HttpResponse(
            status_code=self.status_code,
            body=self.body,
            headers=MappingProxyType(merged),
            raw_text=self.raw_text,
            content_type=self.content_type,
        )
