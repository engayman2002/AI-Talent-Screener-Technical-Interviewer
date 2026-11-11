"""Outbound Application Port for failed background task dead-letter persistence."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.common.dtos import DeadLetterRecordDTO
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class DeadLetterQueuePort(Protocol):
    """Port capturing terminal or retry-exhausted task envelopes for replay and inspection."""

    async def enqueue(self, record: DeadLetterRecordDTO) -> None: ...

    async def list_by_tenant(
        self, tenant_id: TenantId
    ) -> tuple[DeadLetterRecordDTO, ...]: ...
