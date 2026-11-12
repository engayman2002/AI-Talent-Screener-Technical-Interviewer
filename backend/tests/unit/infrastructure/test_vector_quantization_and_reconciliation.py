"""Unit tests for INT8 Quantization, Outbox Relay Worker & Anti-Entropy Reconciliation."""

from __future__ import annotations

import asyncio
import math
from uuid import UUID

import pytest

from talent_screener.domain.contexts.document_intelligence import (
    CandidateProfile,
    ExtractedSkillClaim,
    FileContentHash,
    PiiRedactionMap,
    ProjectComplexityRecord,
    WorkExperienceEntry,
)
from talent_screener.domain.contexts.document_intelligence.value_objects import (
    SeniorityEstimate,
)
from talent_screener.domain.contexts.identity_governance import (
    ConsentRecord,
    ConsentScope,
    ConsentStatus,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import (
    ConfidenceScore,
    SHA256Hash,
    TenantId,
)
from talent_screener.infrastructure.ai_adapters.vector_store import (
    HashProjectionDenseEmbeddingClient,
    HybridVectorSearchAdapter,
)
from talent_screener.infrastructure.messaging import (
    InMemoryDeadLetterQueue,
    InMemoryDomainEventBus,
)
from talent_screener.infrastructure.persistence.relational import (
    DatabaseConnectionConfig,
    SqlCandidatePiiVaultStore,
    SqlCandidateProfileRepository,
    SqlConsentRecordRepository,
    SqlDatabaseManager,
    SqlTransactionalUnitOfWork,
)
from talent_screener.infrastructure.persistence.vector_store import (
    ChildVectorPoint,
    CrossStoreAntiEntropyReconciler,
    Int8ScalarQuantizer,
    OutboxStatus,
    ParentChunkPayload,
    QuantizedHybridVectorStoreBackend,
    TransactionalOutboxRelayWorker,
    profile_to_dossier,
)


def _make_profile(
    tenant_id: TenantId,
    *,
    skill_id: str = "SKILL_KAFKA",
    quote: str = "Operated multi-AZ Kafka clusters processing 40k events/sec.",
) -> CandidateProfile:
    exp = WorkExperienceEntry(
        role_title="Senior Backend Engineer",
        normalized_role="SENIOR_ENGINEER",
        company_token="[COMP_1]",
        start_year=2022,
        start_month=1,
        is_current=True,
    )
    proj = ProjectComplexityRecord(
        project_name="Stream Backbone",
        scale_tier="HIGH_THROUGHPUT_PRODUCTION",
        summary=quote,
        source_quote=quote,
        linked_work_entry_id=exp.id,
    )
    claim = ExtractedSkillClaim(
        canonical_skill_id=skill_id,
        raw_label="Apache Kafka",
        source_quote=quote,
        claimed_depth=5,
        applied_months=36,
        recency_months=0,
        confidence=ConfidenceScore(0.95),
    )
    return CandidateProfile(
        tenant_id=tenant_id,
        anonymized_token=generate_uuid7(),
        file_hash=FileContentHash(
            sha256=SHA256Hash.from_text(quote + str(generate_uuid7())),
            file_size_bytes=2048,
            mime_type="application/pdf",
        ),
        pii_redaction_map=PiiRedactionMap.from_mapping(
            vault_reference_id=generate_uuid7(),
            token_categories={"[PII_NAME_1]": "PERSON_NAME"},
        ),
        work_experiences=(exp,),
        projects=(proj,),
        skill_claims=(claim,),
        seniority_estimate=SeniorityEstimate(
            level="SENIOR",
            verified_experience_months=60,
            confidence=ConfidenceScore(0.90),
        ),
        source_document_text=quote,
    )


@pytest.mark.unit
def test_int8_scalar_quantizer_precision_and_two_stage_rescoring() -> None:
    dims = 64
    client = HashProjectionDenseEmbeddingClient(dimension=dims)
    q_vec = client.embed_query("Kafka distributed streaming architecture")
    q_bytes = Int8ScalarQuantizer.quantize(q_vec, normalize=True)

    # 4x memory compression: 1 byte per float32 dimension
    assert len(q_bytes) == dims
    restored = Int8ScalarQuantizer.dequantize(q_bytes)
    assert Int8ScalarQuantizer.cosine_similarity_float32(q_vec, restored) >= 0.99

    backend = QuantizedHybridVectorStoreBackend()
    tenant = TenantId.generate()
    req_id = generate_uuid7()
    cand_id = generate_uuid7()
    texts = [
        "CSS color palette styling utility",
        "Kafka distributed streaming event bus with partitioning",
        "SQLite B-tree ACID storage internals",
        "PostgreSQL vacuum and indexing maintenance",
        "Kafka producer idempotent batching and replication",
    ]
    dense_vecs = client.embed_documents(texts)
    parent_id = generate_uuid7()
    points = [
        ChildVectorPoint(
            point_id=generate_uuid7(),
            parent_chunk_id=parent_id,
            tenant_id=str(tenant.value),
            candidate_id=str(cand_id),
            requisition_id=str(req_id),
            section_type="EXPERIENCE",
            skill_tags=("SKILL_KAFKA",),
            last_used_year=2026,
            recency_months_delta=0,
            evidentiary_depth=4,
            sync_version=1,
            raw_excerpt=t,
            contextualized_text=t,
            dense_vector=d,
            sparse_vector=(),
        )
        for t, d in zip(texts, dense_vecs, strict=True)
    ]

    async def _run() -> None:
        await backend.upsert_candidate_chunks(
            tenant_id=tenant,
            candidate_profile_id=cand_id,
            sync_version=1,
            parent_chunks=(
                ParentChunkPayload(
                    parent_chunk_id=parent_id,
                    tenant_id=str(tenant.value),
                    candidate_id=str(cand_id),
                    requisition_id=str(req_id),
                    section_type="EXPERIENCE",
                    heading="Work",
                    content="Kafka work",
                    skill_tags=("SKILL_KAFKA",),
                    last_used_year=2026,
                    sync_version=1,
                ),
            ),
            child_points=points,
        )
        hits = await backend.search_quantized(
            tenant_id=tenant,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
            query_vector=q_vec,
            top_k=2,
            oversample_factor=3.0,
        )
        assert len(hits) == 2
        assert "Kafka" in hits[0].point.raw_excerpt
        assert hits[0].exact_float32_score >= hits[1].exact_float32_score
        # Verify quantization stored signed int8 bytes alongside float32 point
        q_payloads = backend.list_quantized_payloads(
            tenant_id=tenant, candidate_profile_id=cand_id
        )
        assert all(len(p.quantized_int8) == dims for p in q_payloads)
        assert math.isfinite(hits[0].quantized_approx_score)

    asyncio.run(_run())


@pytest.mark.unit
def test_quantized_backend_ignores_stale_sync_version_updates() -> None:
    asyncio.run(_run_quantized_backend_stale_sync())


async def _run_quantized_backend_stale_sync() -> None:
    db = SqlDatabaseManager(DatabaseConnectionConfig(dsn=":memory:"))
    backend = QuantizedHybridVectorStoreBackend(db=db)
    tenant = TenantId.generate()
    req_id = generate_uuid7()
    cand_id = generate_uuid7()

    def _pt(excerpt: str, ver: int) -> ChildVectorPoint:
        return ChildVectorPoint(
            point_id=generate_uuid7(),
            parent_chunk_id=generate_uuid7(),
            tenant_id=str(tenant.value),
            candidate_id=str(cand_id),
            requisition_id=str(req_id),
            section_type="EXPERIENCE",
            skill_tags=("LANG_PYTHON",),
            last_used_year=2026,
            recency_months_delta=0,
            evidentiary_depth=3,
            sync_version=ver,
            raw_excerpt=excerpt,
            contextualized_text=excerpt,
            dense_vector=(1.0, 0.0, 0.0, 0.0),
            sparse_vector=(("python", 1.0),),
        )

    try:
        await backend.upsert_candidate_chunks(
            tenant_id=tenant,
            candidate_profile_id=cand_id,
            sync_version=3,
            parent_chunks=(),
            child_points=(_pt("Fresh version 3", 3),),
        )
        # Stale out-of-order version=1 must be silently ignored
        await backend.upsert_candidate_chunks(
            tenant_id=tenant,
            candidate_profile_id=cand_id,
            sync_version=1,
            parent_chunks=(),
            child_points=(_pt("Stale version 1", 1),),
        )
        pts = await backend.list_candidate_points(
            tenant_id=tenant, requisition_id=req_id, candidate_profile_id=cand_id
        )
        assert len(pts) == 1
        assert pts[0].raw_excerpt == "Fresh version 3"
        assert backend.get_sync_version(tenant_id=tenant, candidate_profile_id=cand_id) == 3
    finally:
        db.close()


@pytest.mark.unit
def test_outbox_relay_synchronizes_vectors_retries_and_routes_to_dlq() -> None:
    asyncio.run(_run_outbox_relay_and_dlq())


async def _run_outbox_relay_and_dlq() -> None:
    db = SqlDatabaseManager(DatabaseConnectionConfig(dsn=":memory:"))
    bus = InMemoryDomainEventBus()
    dlq = InMemoryDeadLetterQueue()
    profile_repo = SqlCandidateProfileRepository(db)
    backend = QuantizedHybridVectorStoreBackend(db=db)
    vector_search = HybridVectorSearchAdapter(backend=backend)
    pii_vault = SqlCandidatePiiVaultStore(db, master_kek=b"M" * 32)
    uow = SqlTransactionalUnitOfWork(
        db=db, event_bus=bus, dispatch_to_event_bus_on_commit=False
    )
    tenant = TenantId.generate()

    try:
        profile = _make_profile(tenant)
        async with uow:
            await uow.begin(tenant)
            profile_repo.save(profile)
            uow.register_aggregate(profile)
            await uow.commit()

        assert backend.get_sync_version(tenant_id=tenant, candidate_profile_id=profile.id) is None

        relay = TransactionalOutboxRelayWorker(
            db=db,
            profile_repo=profile_repo,
            vector_search=vector_search,
            vector_backend=backend,
            pii_vault=pii_vault,
            event_bus=bus,
            dlq=dlq,
            max_retries=2,
            base_retry_delay_seconds=0.0,
            relay_to_event_bus=True,
        )
        receipt = await relay.poll_and_dispatch(tenant_id=tenant)
        assert receipt.published_count == 1
        assert backend.get_sync_version(tenant_id=tenant, candidate_profile_id=profile.id) == 1
        assert len(bus.published_events) == 1

        # Second poll finds nothing pending
        receipt2 = await relay.poll_and_dispatch(tenant_id=tenant)
        assert receipt2.polled_count == 0

        # Failing subscriber exercises FAILED_RETRYABLE -> DEAD_LETTERED -> DLQ
        class _FailingHandler:
            async def handle(self, event: object) -> None:
                raise ConnectionError("Downstream vector endpoint timeout")

        consent = ConsentRecord(
            tenant_id=tenant,
            candidate_id=profile.id,
            granted_scopes=(ConsentScope.AI_SCREENING,),
        )
        consent.request_erasure(reason="GDPR Art 17")
        consent_repo = SqlConsentRecordRepository(db)
        async with uow:
            await uow.begin(tenant)
            consent_repo.save(consent)
            uow.register_aggregate(consent)
            await uow.commit()

        from talent_screener.domain.contexts.identity_governance import (
            CandidateErasureRequestedEvent,
        )

        bus.subscribe(CandidateErasureRequestedEvent, _FailingHandler())

        attempt1 = await relay.poll_and_dispatch(tenant_id=tenant)
        assert attempt1.retry_scheduled_count == 1
        assert len(relay.list_records(tenant_id=tenant, status=OutboxStatus.FAILED_RETRYABLE)) == 1

        attempt2 = await relay.poll_and_dispatch(tenant_id=tenant)
        assert attempt2.dead_lettered_count == 1
        assert len(relay.list_records(tenant_id=tenant, status=OutboxStatus.DEAD_LETTERED)) == 1
        dlq_items = await dlq.list_by_tenant(tenant)
        assert len(dlq_items) == 1
        assert dlq_items[0].error_code == "OUTBOX_RELAY_EXHAUSTED"
    finally:
        db.close()


@pytest.mark.unit
def test_anti_entropy_reconciler_heals_missing_stale_orphan_and_erasure_drift() -> None:
    asyncio.run(_run_anti_entropy_reconciler())


async def _run_anti_entropy_reconciler() -> None:
    db = SqlDatabaseManager(DatabaseConnectionConfig(dsn=":memory:"))
    profile_repo = SqlCandidateProfileRepository(db)
    consent_repo = SqlConsentRecordRepository(db)
    backend = QuantizedHybridVectorStoreBackend(db=db)
    vector_search = HybridVectorSearchAdapter(backend=backend)
    pii_vault = SqlCandidatePiiVaultStore(db, master_kek=b"R" * 32)
    tenant = TenantId.generate()
    req_id = generate_uuid7()

    try:
        # 1. Missing vector profile (in SQL v1, missing in vector store)
        missing_prof = _make_profile(tenant, skill_id="SKILL_POSTGRES")
        profile_repo.save(missing_prof)

        # 2. Stale vector profile (SQL version bumped to 3, vector indexed at version 1)
        stale_prof = _make_profile(tenant, skill_id="SKILL_REDIS")
        profile_repo.save(stale_prof)
        stale_pts_ver = 1
        await vector_search.index_candidate_dossier(
            tenant_id=tenant,
            requisition_id=req_id,
            candidate_profile_id=stale_prof.id,
            dossier=profile_to_dossier(stale_prof),
            sync_version=stale_pts_ver,
        )
        # Bump SQL version of stale_prof to 2
        db.execute(
            "UPDATE candidate_profiles SET version = 2 WHERE profile_id = ? AND tenant_id = ?",
            (str(stale_prof.id), str(tenant.value)),
        )

        # 3. Orphan vector (present in vector store, deleted from SQL)
        orphan_id: UUID = generate_uuid7()
        orphan_pt = ChildVectorPoint(
            point_id=generate_uuid7(),
            parent_chunk_id=generate_uuid7(),
            tenant_id=str(tenant.value),
            candidate_id=str(orphan_id),
            requisition_id=str(req_id),
            section_type="EXPERIENCE",
            skill_tags=("SKILL_ORPHAN",),
            last_used_year=2025,
            recency_months_delta=12,
            evidentiary_depth=2,
            sync_version=1,
            raw_excerpt="Orphaned chunk",
            contextualized_text="Orphaned chunk",
            dense_vector=(0.5, 0.5, 0.5, 0.5),
            sparse_vector=(),
        )
        await backend.upsert_candidate_chunks(
            tenant_id=tenant,
            candidate_profile_id=orphan_id,
            sync_version=1,
            parent_chunks=(),
            child_points=(orphan_pt,),
        )

        # 4. Incomplete GDPR erasure: ERASURE_REQUESTED + unshredded vault + vectors present
        erased_prof = _make_profile(tenant, skill_id="SKILL_ERASED")
        profile_repo.save(erased_prof)
        await pii_vault.tokenize_and_vault(
            tenant_id=tenant,
            candidate_profile_id=erased_prof.id,
            raw_text="Email: erase.me@example.com",
        )
        await backend.upsert_candidate_chunks(
            tenant_id=tenant,
            candidate_profile_id=erased_prof.id,
            sync_version=1,
            parent_chunks=(),
            child_points=(
                ChildVectorPoint(
                    point_id=generate_uuid7(),
                    parent_chunk_id=generate_uuid7(),
                    tenant_id=str(tenant.value),
                    candidate_id=str(erased_prof.id),
                    requisition_id=str(req_id),
                    section_type="SKILLS",
                    skill_tags=("SKILL_ERASED",),
                    last_used_year=2025,
                    recency_months_delta=0,
                    evidentiary_depth=2,
                    sync_version=1,
                    raw_excerpt="To be erased",
                    contextualized_text="To be erased",
                    dense_vector=(0.5, 0.5, 0.5, 0.5),
                    sparse_vector=(),
                ),
            ),
        )
        consent = ConsentRecord(
            tenant_id=tenant,
            candidate_id=erased_prof.id,
            granted_scopes=(ConsentScope.AI_SCREENING,),
        )
        consent.request_erasure(reason="Right to erasure")
        consent_repo.save(consent)

        reconciler = CrossStoreAntiEntropyReconciler(
            db=db,
            profile_repo=profile_repo,
            consent_repo=consent_repo,
            vector_backend=backend,
            vector_search=vector_search,
            pii_vault=pii_vault,
        )
        report = await reconciler.reconcile_tenant(tenant, default_requisition_id=req_id)

        assert report.missing_healed == 1
        assert report.stale_healed == 1
        assert report.orphans_purged == 1
        assert report.erasures_completed >= 1

        # Verify repaired end state across all stores
        assert backend.get_sync_version(tenant_id=tenant, candidate_profile_id=missing_prof.id) == 1
        assert backend.get_sync_version(tenant_id=tenant, candidate_profile_id=stale_prof.id) == 2
        assert backend.get_sync_version(tenant_id=tenant, candidate_profile_id=orphan_id) is None
        assert (
            backend.get_sync_version(tenant_id=tenant, candidate_profile_id=erased_prof.id)
            is None
        )

        shred_audit = pii_vault.get_audit_snapshot(tenant_id=tenant, candidate_id=erased_prof.id)
        assert shred_audit is not None and shred_audit.is_shredded is True
        updated_consent = consent_repo.get_by_candidate_id(tenant, erased_prof.id)
        assert updated_consent is not None
        assert updated_consent.status is ConsentStatus.CRYPTO_SHREDDED

        # Idempotent clean second pass reports zero drift
        clean_report = await reconciler.reconcile_tenant(tenant, default_requisition_id=req_id)
        assert clean_report.total_drift_repaired == 0
    finally:
        db.close()
