"""Stage 5 & 6 Multi-Tenant Hybrid Vector Search Adapter implementing HybridVectorSearchPort."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from uuid import UUID

from talent_screener.application.common.exceptions import UnauthorizedTenantAccessError
from talent_screener.application.document_intelligence.dtos import (
    CandidateRetrievalEvidenceBundleDTO,
    CompetencyQueryDescriptorDTO,
    CompetencyRetrievalEvidenceDTO,
    ExtractedCandidateDossierDTO,
    RetrievedChunkScoreDTO,
)
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.ai_adapters.langchain_parser.normalizer import (
    DocumentSectionType,
)
from talent_screener.infrastructure.ai_adapters.vector_store.chunker import (
    HierarchicalDocumentChunker,
)
from talent_screener.infrastructure.ai_adapters.vector_store.encoders import (
    Bm25SparseEncoder,
    DenseEmbeddingClientProtocol,
    DenseVectorMath,
    HashProjectionDenseEmbeddingClient,
)
from talent_screener.infrastructure.persistence.vector_store.in_memory_backend import (
    ChildVectorPoint,
    InMemoryHybridVectorStoreBackend,
    ParentChunkPayload,
    VectorStoreBackendProtocol,
)

DEFAULT_SECTION_IMPORTANCE_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        DocumentSectionType.EXPERIENCE.value: 1.00,
        DocumentSectionType.PROJECTS.value: 0.95,
        DocumentSectionType.CERTIFICATIONS.value: 0.78,
        DocumentSectionType.SKILLS.value: 0.72,
        DocumentSectionType.EDUCATION.value: 0.65,
        DocumentSectionType.SUMMARY.value: 0.60,
        DocumentSectionType.OTHER.value: 0.58,
    }
)


def _require_tenant(tenant_id: object) -> TenantId:
    if not isinstance(tenant_id, TenantId) or not isinstance(tenant_id.value, UUID):
        raise UnauthorizedTenantAccessError(
            "Valid TenantId is required for hybrid vector operations.",
            code="MISSING_TENANT_CONTEXT",
        )
    return tenant_id


class HybridVectorSearchAdapter:
    """Hybrid dense + BM25 + section-importance adapter fulfilling HybridVectorSearchPort."""

    def __init__(
        self,
        *,
        backend: VectorStoreBackendProtocol | None = None,
        embedding_client: DenseEmbeddingClientProtocol | None = None,
        chunker: HierarchicalDocumentChunker | None = None,
        bm25_encoder: Bm25SparseEncoder | None = None,
        section_weights: Mapping[str, float] | None = None,
        dense_rank_weight: float = 0.60,
        sparse_rank_weight: float = 0.40,
        min_relevance_score: float = 0.20,
    ) -> None:
        self._backend: VectorStoreBackendProtocol = (
            backend if backend is not None else InMemoryHybridVectorStoreBackend()
        )
        self._embedding_client: DenseEmbeddingClientProtocol = (
            embedding_client
            if embedding_client is not None
            else HashProjectionDenseEmbeddingClient()
        )
        self._chunker = chunker if chunker is not None else HierarchicalDocumentChunker()
        self._bm25 = bm25_encoder if bm25_encoder is not None else Bm25SparseEncoder()
        self._section_weights = (
            dict(section_weights)
            if section_weights is not None
            else dict(DEFAULT_SECTION_IMPORTANCE_WEIGHTS)
        )
        self._dense_weight = dense_rank_weight
        self._sparse_weight = sparse_rank_weight
        self._min_relevance_score = min_relevance_score
        self._idf_by_candidate: dict[tuple[UUID, UUID], tuple[dict[str, float], int]] = {}

    async def index_candidate_dossier(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        dossier: ExtractedCandidateDossierDTO,
        sync_version: int,
    ) -> None:
        """Segment dossier into Parent/Child chunks, dual-encode vectors, and upsert."""
        valid_tenant = _require_tenant(tenant_id)
        bundle = self._chunker.chunk_candidate_dossier(
            tenant_id=valid_tenant,
            requisition_id=requisition_id,
            candidate_id=candidate_profile_id,
            dossier=dossier,
            sync_version=sync_version,
        )

        child_texts = [c.contextualized_text for c in bundle.child_chunks]
        dense_vectors = (
            self._embedding_client.embed_documents(child_texts) if child_texts else ()
        )
        sparse_vectors, idf_map = self._bm25.encode_corpus(
            [c.raw_excerpt for c in bundle.child_chunks]
        )
        self._idf_by_candidate[(valid_tenant.value, candidate_profile_id)] = (
            idf_map,
            len(bundle.child_chunks),
        )

        t_str = str(valid_tenant.value)
        cand_str = str(candidate_profile_id)
        req_str = str(requisition_id)

        parent_payloads = [
            ParentChunkPayload(
                parent_chunk_id=par.parent_chunk_id,
                tenant_id=t_str,
                candidate_id=cand_str,
                requisition_id=req_str,
                section_type=par.section_type.value,
                heading=par.heading,
                content=par.content,
                skill_tags=par.skill_tags,
                last_used_year=par.last_used_year,
                sync_version=sync_version,
            )
            for par in bundle.parent_chunks
        ]

        child_points = [
            ChildVectorPoint(
                point_id=child.chunk_id,
                parent_chunk_id=child.parent_chunk_id,
                tenant_id=t_str,
                candidate_id=cand_str,
                requisition_id=req_str,
                section_type=child.section_type.value,
                skill_tags=child.skill_tags,
                last_used_year=child.last_used_year,
                recency_months_delta=child.recency_months_delta,
                evidentiary_depth=child.evidentiary_depth,
                sync_version=sync_version,
                raw_excerpt=child.raw_excerpt,
                contextualized_text=child.contextualized_text,
                dense_vector=DenseVectorMath.l2_normalize(dense_vec),
                sparse_vector=sparse_vec,
            )
            for child, dense_vec, sparse_vec in zip(
                bundle.child_chunks, dense_vectors, sparse_vectors, strict=True
            )
        ]

        await self._backend.upsert_candidate_chunks(
            tenant_id=valid_tenant,
            candidate_profile_id=candidate_profile_id,
            sync_version=sync_version,
            parent_chunks=parent_payloads,
            child_points=child_points,
        )

    @staticmethod
    def _build_query_text(query: CompetencyQueryDescriptorDTO) -> str:
        skill_phrase = (
            query.canonical_skill_id.removeprefix("SKILL_")
            .removeprefix("LANG_")
            .replace("_", " ")
        )
        anchor = query.ideal_anchor_descriptor.strip()
        if anchor and skill_phrase.lower() not in anchor.lower():
            return f"{skill_phrase} {anchor}"
        return anchor or skill_phrase

    async def search_competency_evidence(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        queries: Sequence[CompetencyQueryDescriptorDTO],
        top_k: int = 5,
    ) -> CandidateRetrievalEvidenceBundleDTO:
        """Execute tenant-isolated hybrid dense + BM25 + section-weighted retrieval."""
        valid_tenant = _require_tenant(tenant_id)
        points = await self._backend.list_candidate_points(
            tenant_id=valid_tenant,
            requisition_id=requisition_id,
            candidate_profile_id=candidate_profile_id,
        )
        idf_map, doc_count = self._idf_by_candidate.get(
            (valid_tenant.value, candidate_profile_id),
            ({}, max(1, len(points))),
        )

        competency_evidence: list[CompetencyRetrievalEvidenceDTO] = []
        for query in queries:
            query_text = self._build_query_text(query)
            query_dense = DenseVectorMath.l2_normalize(
                self._embedding_client.embed_query(query_text)
            )
            scored_chunks: list[tuple[float, RetrievedChunkScoreDTO]] = []

            for pt in points:
                # Defense-in-depth tenant assertion prior to any scoring calculation.
                if pt.tenant_id != str(valid_tenant.value):
                    raise UnauthorizedTenantAccessError(
                        "Vector payload tenant_id mismatch detected during search.",
                        code="CROSS_TENANT_VECTOR_LEAK",
                    )
                raw_dense = DenseVectorMath.cosine_similarity(query_dense, pt.dense_vector)
                raw_sparse = self._bm25.score_query(
                    query_text=query_text,
                    doc_sparse_vector=pt.sparse_vector,
                    idf_map=idf_map,
                    corpus_doc_count=doc_count,
                )
                sec_weight = self._section_weights.get(pt.section_type, 0.70)
                weighted_dense = max(0.0, min(1.0, raw_dense * sec_weight))
                weighted_sparse = max(0.0, min(1.0, raw_sparse * sec_weight))
                skill_bonus = (
                    0.08 if query.canonical_skill_id in pt.skill_tags else 0.0
                )
                rank_score = (
                    self._dense_weight * weighted_dense
                    + self._sparse_weight * weighted_sparse
                    + skill_bonus
                )
                scored_chunks.append(
                    (
                        rank_score,
                        RetrievedChunkScoreDTO(
                            chunk_id=str(pt.point_id),
                            parent_entry_id=str(pt.parent_chunk_id),
                            dense_similarity=weighted_dense,
                            sparse_bm25_score=weighted_sparse,
                            recency_months_delta=pt.recency_months_delta,
                            evidentiary_depth=pt.evidentiary_depth,
                            verbatim_excerpt=pt.raw_excerpt,
                        ),
                    )
                )

            scored_chunks.sort(key=lambda pair: pair[0], reverse=True)
            relevant = [
                pair for pair in scored_chunks if pair[0] >= self._min_relevance_score
            ]
            selected = relevant if relevant else (
                scored_chunks[:1] if scored_chunks and scored_chunks[0][0] > 0.0 else []
            )
            top_items = tuple(dto for _, dto in selected[: max(1, top_k)])
            competency_evidence.append(
                CompetencyRetrievalEvidenceDTO(
                    competency_id=query.competency_id,
                    retrieved_chunks=top_items,
                )
            )

        return CandidateRetrievalEvidenceBundleDTO(
            tenant_id=valid_tenant,
            requisition_id=requisition_id,
            candidate_profile_id=candidate_profile_id,
            competency_evidence=tuple(competency_evidence),
        )

    async def delete_candidate_vectors(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
    ) -> None:
        """Permanently remove all Parent and Child chunks for a candidate (GDPR Art. 17)."""
        valid_tenant = _require_tenant(tenant_id)
        await self._backend.delete_by_candidate(
            tenant_id=valid_tenant,
            candidate_profile_id=candidate_profile_id,
        )
        self._idf_by_candidate.pop((valid_tenant.value, candidate_profile_id), None)
