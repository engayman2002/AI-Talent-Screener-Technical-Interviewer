"""Identity & Tenant Governance Bounded Context Repository Ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.domain.contexts.identity_governance.aggregates import (
    ConsentRecord,
    TenantAccount,
)
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class TenantAccountRepositoryPort(Protocol):
    """Persistence contract for TenantAccount aggregates."""

    def save(self, tenant: TenantAccount) -> None: ...

    def get_by_id(self, tenant_id: TenantId) -> TenantAccount | None: ...

    def get_by_slug(self, organization_slug: str) -> TenantAccount | None: ...


@runtime_checkable
class ConsentRecordRepositoryPort(Protocol):
    """Persistence contract for ConsentRecord aggregates."""

    def save(self, record: ConsentRecord) -> None: ...

    def get_by_id(self, tenant_id: TenantId, record_id: UUID) -> ConsentRecord | None: ...

    def get_by_candidate_id(
        self,
        tenant_id: TenantId,
        candidate_id: UUID,
    ) -> ConsentRecord | None: ...
