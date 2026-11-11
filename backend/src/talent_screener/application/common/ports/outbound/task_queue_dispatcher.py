"""Outbound Application Port for asynchronous distributed worker task enqueueing."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from talent_screener.application.common.dtos import TaskDispatchDTO


@runtime_checkable
class TaskQueueDispatcherPort(Protocol):
    """Port decoupling batch and async job dispatch from Celery/ARQ/Redis brokers."""

    async def dispatch(self, task: TaskDispatchDTO) -> str: ...

    async def dispatch_batch(self, tasks: Sequence[TaskDispatchDTO]) -> tuple[str, ...]: ...
