"""Observability tracing & FinOps telemetry in-memory adapter."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager

from talent_screener.application.common.dtos import TokenUsageRecordDTO
from talent_screener.domain.shared.value_objects import TenantId


class _InMemoryTraceSpan(AbstractContextManager["_InMemoryTraceSpan"]):
    def __init__(self, name: str, attributes: Mapping[str, object] | None = None) -> None:
        self.name = name
        self.attributes: dict[str, object] = dict(attributes) if attributes is not None else {}

    def __enter__(self) -> _InMemoryTraceSpan:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class InMemoryObservabilityTracer:
    """In-memory ObservabilityTracerPort recording structured spans, metrics, and token usage."""

    def __init__(self) -> None:
        self.spans: list[_InMemoryTraceSpan] = []
        self.token_usages: list[TokenUsageRecordDTO] = []
        self.metrics: list[tuple[str, float, dict[str, str]]] = []

    def start_span(
        self,
        *,
        name: str,
        tenant_id: TenantId,
        correlation_id: str,
        attributes: Mapping[str, object] | None = None,
    ) -> _InMemoryTraceSpan:
        attrs = dict(attributes) if attributes is not None else {}
        attrs["tenant_id"] = str(tenant_id.value)
        attrs["correlation_id"] = correlation_id
        span = _InMemoryTraceSpan(name, attrs)
        self.spans.append(span)
        return span

    def record_token_usage(self, usage: TokenUsageRecordDTO) -> None:
        self.token_usages.append(usage)

    def record_metric(
        self,
        *,
        name: str,
        value: float,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        self.metrics.append((name, value, dict(tags) if tags is not None else {}))
