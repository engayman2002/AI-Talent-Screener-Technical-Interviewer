"""Outbound Application Port for OpenTelemetry-compatible tracing and FinOps metrics."""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from talent_screener.application.common.dtos import TokenUsageRecordDTO
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class TraceSpanPort(Protocol):
    """Context-managed trace span abstraction."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def set_attribute(self, key: str, value: object) -> None: ...


@runtime_checkable
class ObservabilityTracerPort(Protocol):
    """Vendor-agnostic observability and FinOps metering port."""

    def start_span(
        self,
        *,
        name: str,
        tenant_id: TenantId,
        correlation_id: str,
        attributes: Mapping[str, object] | None = None,
    ) -> TraceSpanPort: ...

    def record_token_usage(self, usage: TokenUsageRecordDTO) -> None: ...

    def record_metric(
        self,
        *,
        name: str,
        value: float,
        tags: Mapping[str, str] | None = None,
    ) -> None: ...
