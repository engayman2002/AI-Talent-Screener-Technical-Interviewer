"""Recruitment Management Bounded Context Repository Port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.domain.contexts.recruitment.aggregates import JobRequisition
from talent_screener.domain.contexts.recruitment.value_objects import RequisitionStatus
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class JobRequisitionRepositoryPort(Protocol):
    """Persistence contract for JobRequisition aggregate roots."""

    def save(self, requisition: JobRequisition) -> None: ...

    def get_by_id(self, tenant_id: TenantId, requisition_id: UUID) -> JobRequisition | None: ...

    def list_by_tenant(
        self,
        tenant_id: TenantId,
        *,
        status: RequisitionStatus | None = None,
    ) -> tuple[JobRequisition, ...]: ...
