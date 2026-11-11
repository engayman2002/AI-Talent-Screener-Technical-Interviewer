"""Document Intelligence Bounded Context Repository Ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.aggregates import (
    CandidateProfile,
    ScreeningBatch,
)
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class ScreeningBatchRepositoryPort(Protocol):
    """Persistence contract for ScreeningBatch aggregates."""

    def save(self, batch: ScreeningBatch) -> None: ...

    def get_by_id(self, tenant_id: TenantId, batch_id: UUID) -> ScreeningBatch | None: ...


@runtime_checkable
class CandidateProfileRepositoryPort(Protocol):
    """Persistence contract for CandidateProfile aggregates."""

    def save(self, profile: CandidateProfile) -> None: ...

    def get_by_id(self, tenant_id: TenantId, profile_id: UUID) -> CandidateProfile | None: ...
