"""ISP-segregated read-only query port for CandidateProfile projections."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.document_intelligence.dtos import CandidateProfileReadDTO
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class CandidateProfileReadModelPort(Protocol):
    """Read-only query contract returning zero-PII CandidateProfile projections."""

    async def get_profile_read_model(
        self,
        *,
        tenant_id: TenantId,
        profile_id: UUID,
    ) -> CandidateProfileReadDTO | None: ...
