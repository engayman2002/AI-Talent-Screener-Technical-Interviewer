"""Replayable Server-Sent Events (SSE) Progress Broker fulfilling Publisher & Stream Ports."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from uuid import UUID

from talent_screener.application.common.dtos import (
    RealtimeProgressEventDTO,
    SequencedProgressEventDTO,
)
from talent_screener.domain.shared.value_objects import TenantId


@dataclass(frozen=True, slots=True)
class SseChannelSubscription:
    """Handle for async subscribers receiving live sequenced progress events."""

    tenant_id: TenantId
    channel: str
    queue: asyncio.Queue[SequencedProgressEventDTO]


class ReplayableSseProgressBroker:
    """Thread/async-safe tenant-scoped progress broker supporting Last-Event-ID replay."""

    def __init__(self, *, ring_buffer_size: int = 1000) -> None:
        if ring_buffer_size <= 0:
            raise ValueError("ring_buffer_size must be positive.")
        self._ring_size = ring_buffer_size
        self._seq_by_channel: dict[tuple[UUID, str], int] = {}
        self._buffer_by_channel: dict[tuple[UUID, str], deque[SequencedProgressEventDTO]] = {}
        self._subscribers: dict[
            tuple[UUID, str], list[asyncio.Queue[SequencedProgressEventDTO]]
        ] = {}

    async def publish_progress(self, event: RealtimeProgressEventDTO) -> None:
        """Assign monotonic event_id, buffer per (tenant, channel), and fan out to listeners."""
        key = (event.tenant_id.value, event.channel)
        next_seq = self._seq_by_channel.get(key, 0) + 1
        self._seq_by_channel[key] = next_seq

        sequenced = SequencedProgressEventDTO(
            event_id=next_seq,
            tenant_id=event.tenant_id,
            channel=event.channel,
            event_type=event.event_type,
            payload=event.payload,
            emitted_at=event.emitted_at,
        )
        ring = self._buffer_by_channel.get(key)
        if ring is None:
            ring = deque(maxlen=self._ring_size)
            self._buffer_by_channel[key] = ring
        ring.append(sequenced)

        for queue in self._subscribers.get(key, ()):
            await queue.put(sequenced)

    def replay_since(
        self,
        *,
        tenant_id: TenantId,
        channel: str,
        last_event_id: int | None = None,
    ) -> tuple[SequencedProgressEventDTO, ...]:
        """Return buffered events strictly after ``last_event_id`` in ascending order."""
        ring = self._buffer_by_channel.get((tenant_id.value, channel))
        if not ring:
            return ()
        if last_event_id is None:
            return tuple(ring)
        return tuple(item for item in ring if item.event_id > last_event_id)

    def subscribe(self, *, tenant_id: TenantId, channel: str) -> SseChannelSubscription:
        """Attach a live subscriber queue scoped to ``(tenant_id, channel)``."""
        key = (tenant_id.value, channel)
        queue: asyncio.Queue[SequencedProgressEventDTO] = asyncio.Queue()
        self._subscribers.setdefault(key, []).append(queue)
        return SseChannelSubscription(tenant_id=tenant_id, channel=channel, queue=queue)

    def unsubscribe(self, subscription: SseChannelSubscription) -> None:
        """Detach subscriber queue cleanly."""
        key = (subscription.tenant_id.value, subscription.channel)
        listeners = self._subscribers.get(key)
        if not listeners:
            return
        if subscription.queue in listeners:
            listeners.remove(subscription.queue)

    def latest_event_id(self, *, tenant_id: TenantId, channel: str) -> int:
        """Return latest assigned event sequence ID for ``(tenant_id, channel)`` (0 if none)."""
        return self._seq_by_channel.get((tenant_id.value, channel), 0)
