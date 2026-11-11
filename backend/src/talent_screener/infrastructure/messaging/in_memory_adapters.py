"""In-memory task queue dispatcher and real-time progress publisher adapters."""

from __future__ import annotations

from collections.abc import Sequence

from talent_screener.application.common.dtos import (
    RealtimeProgressEventDTO,
    TaskDispatchDTO,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7


class InMemoryTaskQueueDispatcher:
    """Deterministic in-memory TaskQueueDispatcherPort recording queued tasks."""

    def __init__(self) -> None:
        self._dispatched: list[TaskDispatchDTO] = []

    @property
    def dispatched_tasks(self) -> tuple[TaskDispatchDTO, ...]:
        return tuple(self._dispatched)

    async def dispatch(self, task: TaskDispatchDTO) -> str:
        self._dispatched.append(task)
        return f"task-{generate_uuid7()}"

    async def dispatch_batch(self, tasks: Sequence[TaskDispatchDTO]) -> tuple[str, ...]:
        ids: list[str] = []
        for task in tasks:
            ids.append(await self.dispatch(task))
        return tuple(ids)


class InMemoryRealtimeProgressPublisher:
    """In-memory RealtimeProgressPublisherPort buffering SSE/WebSocket progress events."""

    def __init__(self) -> None:
        self._events: list[RealtimeProgressEventDTO] = []

    @property
    def events(self) -> tuple[RealtimeProgressEventDTO, ...]:
        return tuple(self._events)

    async def publish_progress(self, event: RealtimeProgressEventDTO) -> None:
        self._events.append(event)
