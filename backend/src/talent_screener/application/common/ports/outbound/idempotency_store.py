"""Outbound Application Port for atomic idempotency key reservation and replay."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.common.dtos import IdempotencyRecordDTO
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class IdempotencyStorePort(Protocol):
    """Contract enforcing idempotent mutation semantics across retries and duplicate uploads."""

    async def check_and_reserve(
        self,
        *,
        tenant_id: TenantId,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> IdempotencyRecordDTO | None: ...

    async def mark_completed(
        self,
        *,
        tenant_id: TenantId,
        operation: str,
        idempotency_key: str,
        resource_id: UUID,
        serialized_result: Mapping[str, object] | None = None,
    ) -> None: ...

    async def release_reservation(
        self,
        *,
        tenant_id: TenantId,
        operation: str,
        idempotency_key: str,
    ) -> None: ...
