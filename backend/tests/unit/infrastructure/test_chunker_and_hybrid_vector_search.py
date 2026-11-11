"""Unit tests for Phase 3 Hierarchical Chunking, BM25/Dense Encoders & Hybrid Search."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest

from talent_screener.application.common.exceptions import UnauthorizedTenantAccessError
from talent_screener.application.document_intelligence.dtos import (
    CompetencyQueryDescriptorDTO,
    ExtractedCandidateDossierDTO,
    ExtractedWorkExperienceDTO,
)
from talent_screener.domain.contexts.document_intelligence import (
    ExtractedSkillClaim,
    PiiRedactionMap,
    SeniorityEstimate,
)
from talent_screener.domain.shared import ConfidenceScore, TenantId, generate_uuid7
from talent_screener.infrastructure.ai_adapters.langchain_parser import (
    DocumentSectionType,
)
from talent_screener.infrastructure.ai_adapters.vector_store import (
    Bm25SparseEncoder,
    DenseVectorMath,
    HashProjectionDenseEmbeddingClient,
    HierarchicalDocumentChunker,
    HybridVectorSearchAdapter,
    tokenize_technical_text,
)
from talent_screener.infrastructure.persistence import InMemoryHybridVectorStoreBackend


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _sample_dossier(raw_text: str) -> ExtractedCandidateDossierDTO:
    cand_id = generate_uuid7()
    return ExtractedCandidateDossierDTO(
        anonymized_candidate_token=cand_id,
        schema_version="1.0.0",
        raw_redacted_text=raw_text,
        pii_redaction_map=PiiRedactionMap.from_mapping(
            vault_reference_id=generate_uuid7(),
            token_categories={"[PII_EMAIL_1]": "EMAIL"},
        ),
        work_experiences=(
            ExtractedWorkExperienceDTO(
                role_title="Senior Distributed Systems Engineer",
                normalized_role="BACKEND_ENGINEER",
                company_token="ORG_1",
                start_year=2021,
                start_month=1,
                end_year=2024,
                end_month=12,
            ),
        ),
        projects=(),
        skill_claims=(
            ExtractedSkillClaim(
                canonical_skill_id="SKILL_LANG_PYTHON",
                raw_label="Python",
                source_quote="Built Python asyncio microservices with PostgreSQL",
                claimed_depth=4,
                applied_months=48,
                recency_months=0,
                confidence=ConfidenceScore(0.95),
            ),
        ),
        seniority_estimate=SeniorityEstimate(
            level="SENIOR",
            verified_experience_months=48,
            confidence=ConfidenceScore(0.92),
        ),
        extraction_confidence=ConfidenceScore(0.94),
    )


@pytest.mark.unit
def test_hierarchical_chunker_produces_parent_child_and_deterministic_uuid5() -> None:
    chunker = HierarchicalDocumentChunker(
        child_min_tokens=250,
        child_target_tokens=300,
        child_max_tokens=360,
        child_overlap_tokens=50,
    )
    long_exp = " ".join(
        [f"Architected Python asyncio distributed worker shard{i}" for i in range(110)]
    )
    raw_cv = (
        "SUMMARY\n"
        "Platform engineer specializing in Python.\n"
        "EXPERIENCE\n"
        f"{long_exp}\n"
        "SKILLS\n"
        "Python, PostgreSQL, Kubernetes\n"
    )
    tid = TenantId.generate()
    req_id = generate_uuid7()
    cand_id = generate_uuid7()
    dossier = _sample_dossier(raw_cv)

    bundle1 = chunker.chunk_candidate_dossier(
        tenant_id=tid,
        requisition_id=req_id,
        candidate_id=cand_id,
        dossier=dossier,
        sync_version=1,
    )
    bundle2 = chunker.chunk_candidate_dossier(
        tenant_id=tid,
        requisition_id=req_id,
        candidate_id=cand_id,
        dossier=dossier,
        sync_version=1,
    )

    assert len(bundle1.parent_chunks) == 3
    exp_children = [
        c for c in bundle1.child_chunks if c.section_type is DocumentSectionType.EXPERIENCE
    ]
    assert len(exp_children) >= 2
    assert all(c.parent_chunk_id == exp_children[0].parent_chunk_id for c in exp_children)
    # Every overlapping window remains bounded within max_tokens.
    assert all(250 <= c.token_count <= 360 for c in exp_children[:-1])
    # Deterministic UUIDv5 identity stability.
    assert [c.chunk_id for c in bundle1.child_chunks] == [
        c.chunk_id for c in bundle2.child_chunks
    ]
    assert bundle1.child_chunks[0].chunk_id.version == 5


@pytest.mark.unit
def test_bm25_and_dense_vector_encoders_score_technical_matches() -> None:
    tokens = tokenize_technical_text("Experienced with C++, C#, .NET, Next.js, and FastAPI")
    assert "c++" in tokens
    assert "c#" in tokens
    assert ".net" in tokens or "net" in tokens
    assert "next.js" in tokens
    assert "fastapi" in tokens

    bm25 = Bm25SparseEncoder(k1=1.5, b=0.75)
    docs = [
        "Built Python asyncio microservices and PostgreSQL databases",
        "Designed React UI dashboards with Figma and Next.js",
    ]
    sparse_vecs, idf_map = bm25.encode_corpus(docs)
    s0 = bm25.score_query(
        query_text="Python asyncio microservices",
        doc_sparse_vector=sparse_vecs[0],
        idf_map=idf_map,
        corpus_doc_count=2,
    )
    s1 = bm25.score_query(
        query_text="Python asyncio microservices",
        doc_sparse_vector=sparse_vecs[1],
        idf_map=idf_map,
        corpus_doc_count=2,
    )
    assert 0.5 < s0 <= 1.0
    assert s1 == 0.0

    embedder = HashProjectionDenseEmbeddingClient(dimension=256)
    doc_embs = embedder.embed_documents(docs)
    q_emb = embedder.embed_query("Python asyncio microservices architecture")
    sim0 = DenseVectorMath.cosine_similarity(q_emb, doc_embs[0])
    sim1 = DenseVectorMath.cosine_similarity(q_emb, doc_embs[1])
    assert sim0 > sim1 + 0.30


@pytest.mark.unit
def test_hybrid_vector_adapter_section_weights_tenant_isolation_and_gdpr_delete() -> None:
    async def _run_test() -> None:
        backend = InMemoryHybridVectorStoreBackend()
        adapter = HybridVectorSearchAdapter(backend=backend)
        tid_a = TenantId.generate()
        tid_b = TenantId.generate()
        req_id = generate_uuid7()
        cand_id = generate_uuid7()

        # Identical keywords in SUMMARY vs EXPERIENCE prove section-importance weighting
        # while unrelated sections (EDUCATION) are excluded by relevance filtering.
        cv_text = (
            "SUMMARY\n"
            "Python asyncio distributed systems engineer.\n"
            "EXPERIENCE\n"
            "Senior Python Platform Engineer 2021-01 to 2024-12\n"
            "Built Python asyncio distributed systems handling 25k req/sec with PostgreSQL.\n"
            "EDUCATION\n"
            "B.Sc. in Fine Arts and Sculpture.\n"
        )
        dossier = _sample_dossier(cv_text)
        await adapter.index_candidate_dossier(
            tenant_id=tid_a,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
            dossier=dossier,
            sync_version=1,
        )

        points = await backend.list_candidate_points(
            tenant_id=tid_a,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
        )
        assert len(points) == 3
        assert {p.section_type for p in points} == {"SUMMARY", "EXPERIENCE", "EDUCATION"}

        # Verify parent chunk retrieval & cross-tenant parent isolation.
        parent = await backend.get_parent_chunk(
            tenant_id=tid_a, parent_chunk_id=points[0].parent_chunk_id
        )
        assert parent is not None
        assert (
            await backend.get_parent_chunk(
                tenant_id=tid_b, parent_chunk_id=points[0].parent_chunk_id
            )
            is None
        )

        # Verify EXPERIENCE outranks SUMMARY while irrelevant EDUCATION is filtered out.
        bundle_a = await adapter.search_competency_evidence(
            tenant_id=tid_a,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
            queries=(
                CompetencyQueryDescriptorDTO(
                    competency_id=generate_uuid7(),
                    canonical_skill_id="SKILL_LANG_PYTHON",
                    ideal_anchor_descriptor="Python asyncio distributed systems",
                    weight=1.0,
                ),
            ),
            top_k=5,
        )
        chunks_a = bundle_a.competency_evidence[0].retrieved_chunks
        assert len(chunks_a) == 2
        assert "Built Python asyncio" in chunks_a[0].verbatim_excerpt
        assert chunks_a[0].dense_similarity > chunks_a[1].dense_similarity

        # Version-guarded upsert: sync_version=2 replaces v1, stale v1 is ignored.
        await adapter.index_candidate_dossier(
            tenant_id=tid_a,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
            dossier=dossier,
            sync_version=2,
        )
        await adapter.index_candidate_dossier(
            tenant_id=tid_a,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
            dossier=dossier,
            sync_version=1,
        )
        v2_points = await backend.list_candidate_points(
            tenant_id=tid_a,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
        )
        assert all(p.sync_version == 2 for p in v2_points)
        assert DenseVectorMath.cosine_similarity((0.0, 0.0), (1.0, 0.0)) == 0.0
        assert DenseVectorMath.cosine_similarity((1.0,), (1.0, 0.0)) == 0.0

        # Tenant B MUST see zero chunks for Candidate in Tenant A (strict isolation).
        bundle_b = await adapter.search_competency_evidence(
            tenant_id=tid_b,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
            queries=(
                CompetencyQueryDescriptorDTO(
                    competency_id=generate_uuid7(),
                    canonical_skill_id="SKILL_LANG_PYTHON",
                    ideal_anchor_descriptor="Python asyncio distributed systems",
                    weight=1.0,
                ),
            ),
        )
        assert bundle_b.competency_evidence[0].retrieved_chunks == ()

        # Missing tenant_id raises UnauthorizedTenantAccessError
        with pytest.raises(UnauthorizedTenantAccessError):
            await adapter.search_competency_evidence(
                tenant_id=None,  # type: ignore[arg-type]
                requisition_id=req_id,
                candidate_profile_id=cand_id,
                queries=(),
            )

        # GDPR deletion removes all tenant-scoped vectors cleanly.
        await adapter.delete_candidate_vectors(tenant_id=tid_a, candidate_profile_id=cand_id)
        remaining = await backend.list_candidate_points(
            tenant_id=tid_a,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
        )
        assert remaining == ()

    _run(_run_test())
