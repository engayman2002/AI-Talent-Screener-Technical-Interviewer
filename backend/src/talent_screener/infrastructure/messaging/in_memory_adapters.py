"""In-memory task queue dispatcher and real-time progress publisher adapters."""

from __future__ import annotations

from collections.abc import Sequence

from talent_screener.application.common.dtos import (
    RealtimeProgressEventDTO,
    TaskDispatchDTO,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7


class InMemoryTaskQueueDispatcher:
    """Deterministic in-memory TaskQueueDispatcherPort with inspectable pending & audit logs."""

    def __init__(self) -> None:
        self._dispatched: list[TaskDispatchDTO] = []
        self._pending: list[TaskDispatchDTO] = []

    @property
    def dispatched_tasks(self) -> tuple[TaskDispatchDTO, ...]:
        return tuple(self._dispatched)

    def pending_count(self) -> int:
        """Return number of queued tasks waiting to be drained by workers."""
        return len(self._pending)

    def pending_tasks(self) -> tuple[TaskDispatchDTO, ...]:
        """Return immutable snapshot of unconsumed queued tasks."""
        return tuple(self._pending)

    def drain_pending(self, max_items: int | None = None) -> tuple[TaskDispatchDTO, ...]:
        """Pop up to ``max_items`` (or all) pending tasks for worker scheduling."""
        if max_items is None or max_items >= len(self._pending):
            drained = tuple(self._pending)
            self._pending.clear()
            return drained
        count = max(0, max_items)
        drained = tuple(self._pending[:count])
        del self._pending[:count]
        return drained

    def requeue(self, task: TaskDispatchDTO) -> None:
        """Push a deferred or backpressured task back onto the pending queue."""
        self._pending.append(task)

    async def dispatch(self, task: TaskDispatchDTO) -> str:
        self._dispatched.append(task)
        self._pending.append(task)
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
