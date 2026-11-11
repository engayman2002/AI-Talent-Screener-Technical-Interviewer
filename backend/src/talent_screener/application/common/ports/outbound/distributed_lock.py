"""Outbound Application Port for fencing-token-aware distributed mutual exclusion."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.common.dtos import FencingLeaseDTO
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class DistributedLockPort(Protocol):
    """Mutual-exclusion lock issuing monotonic fencing tokens to prevent split-brain writes."""

    async def acquire(
        self,
        *,
        tenant_id: TenantId,
        resource_key: str,
        ttl_seconds: int = 30,
    ) -> FencingLeaseDTO: ...

    async def release(self, *, lease: FencingLeaseDTO) -> None: ...
