"""Outbound Application Port for tenant-isolated hybrid dense/sparse vector search."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.document_intelligence.dtos import (
    CandidateRetrievalEvidenceBundleDTO,
    CompetencyQueryDescriptorDTO,
    ExtractedCandidateDossierDTO,
)
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class HybridVectorSearchPort(Protocol):
    """Port abstracting vector indexing and hybrid dense + BM25 retrieval behind an ACL."""

    async def index_candidate_dossier(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        dossier: ExtractedCandidateDossierDTO,
        sync_version: int,
    ) -> None: ...

    async def search_competency_evidence(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        queries: Sequence[CompetencyQueryDescriptorDTO],
        top_k: int = 5,
    ) -> CandidateRetrievalEvidenceBundleDTO: ...

    async def delete_candidate_vectors(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
    ) -> None: ...
