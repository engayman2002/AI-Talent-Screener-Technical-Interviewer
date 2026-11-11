"""Outbound Application Port for SSE/WebSocket real-time progress notifications."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.common.dtos import RealtimeProgressEventDTO


@runtime_checkable
class RealtimeProgressPublisherPort(Protocol):
    """Port broadcasting real-time batch and interview progress frames."""

    async def publish_progress(self, event: RealtimeProgressEventDTO) -> None: ...
