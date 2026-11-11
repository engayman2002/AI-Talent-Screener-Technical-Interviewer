"""Monotonic Fencing-Token DistributedLockPort adapter preventing split-brain mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from talent_screener.application.common.dtos import FencingLeaseDTO
from talent_screener.application.common.exceptions import ResourceConflictError
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import TenantId


@dataclass(frozen=True, slots=True)
class LockLeaseEntry:
    """Tracked lock ownership entry with TTL expiration and monotonic epoch."""

    lease: FencingLeaseDTO
    acquired_at: datetime
    released: bool = False


class FencingTokenDistributedLockAdapter:
    """Mutual-exclusion DistributedLockPort issuing strictly increasing fencing tokens."""

    def __init__(self) -> None:
        self._epochs_by_resource: dict[tuple[UUID, str], int] = {}
        self._active_by_resource: dict[tuple[UUID, str], LockLeaseEntry] = {}

    async def acquire(
        self,
        *,
        tenant_id: TenantId,
        resource_key: str,
        ttl_seconds: int = 30,
    ) -> FencingLeaseDTO:
        """Acquire tenant-scoped resource lock or fail if held by an active non-expired lease."""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive.")
        key = (tenant_id.value, resource_key)
        now = utc_now()
        existing = self._active_by_resource.get(key)
        if existing is not None and not existing.released and existing.lease.expires_at > now:
            raise ResourceConflictError(
                (
                    f"Resource {resource_key!r} is locked by lease {existing.lease.lease_id} "
                    f"(fence={existing.lease.fencing_token})."
                ),
                code="DISTRIBUTED_LOCK_CONTENDED",
                metadata={
                    "resource_key": resource_key,
                    "holder_lease_id": str(existing.lease.lease_id),
                    "holder_fencing_token": existing.lease.fencing_token,
                },
            )

        next_token = self._epochs_by_resource.get(key, 0) + 1
        self._epochs_by_resource[key] = next_token
        lease = FencingLeaseDTO(
            tenant_id=tenant_id,
            resource_key=resource_key,
            fencing_token=next_token,
            lease_id=generate_uuid7(),
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._active_by_resource[key] = LockLeaseEntry(lease=lease, acquired_at=now)
        return lease

    async def release(self, *, lease: FencingLeaseDTO) -> None:
        """Release lock only if ``lease`` still matches the active lease owner."""
        key = (lease.tenant_id.value, lease.resource_key)
        current = self._active_by_resource.get(key)
        if current is None or current.lease.lease_id != lease.lease_id:
            return
        del self._active_by_resource[key]

    def current_fencing_token(self, tenant_id: TenantId, resource_key: str) -> int:
        """Return latest issued monotonic fencing token for ``(tenant_id, resource_key)``."""
        return self._epochs_by_resource.get((tenant_id.value, resource_key), 0)

    def is_token_current(self, lease: FencingLeaseDTO) -> bool:
        """Return True iff ``lease`` carries the latest epoch and has not expired."""
        key = (lease.tenant_id.value, lease.resource_key)
        latest_epoch = self._epochs_by_resource.get(key, 0)
        entry = self._active_by_resource.get(key)
        return (
            entry is not None
            and entry.lease.lease_id == lease.lease_id
            and lease.fencing_token == latest_epoch
            and lease.expires_at > utc_now()
        )
