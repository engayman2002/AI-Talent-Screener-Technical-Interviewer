"""Outbound Application Port for SSE/WebSocket real-time progress notifications."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.common.dtos import (
    RealtimeProgressEventDTO,
    SequencedProgressEventDTO,
)
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class RealtimeProgressPublisherPort(Protocol):
    """Port broadcasting real-time batch and interview progress frames."""

    async def publish_progress(self, event: RealtimeProgressEventDTO) -> None: ...


@runtime_checkable
class RealtimeProgressStreamPort(Protocol):
    """ISP-segregated read/replay port supporting SSE Last-Event-ID resumption."""

    def replay_since(
        self,
        *,
        tenant_id: TenantId,
        channel: str,
        last_event_id: int | None = None,
    ) -> tuple[SequencedProgressEventDTO, ...]: ...
