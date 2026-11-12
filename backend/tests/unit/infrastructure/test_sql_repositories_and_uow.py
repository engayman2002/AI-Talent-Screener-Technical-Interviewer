"""Unit tests for Phase 6 ACID SQL Repositories, Data Mappers, OCC, UoW, and Vault."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from uuid import UUID

import pytest

from talent_screener.application.common.exceptions import (
    IdempotencyConflictError,
    ResourceConflictError,
    ResourceNotFoundError,
    UnauthorizedTenantAccessError,
)
from talent_screener.domain.contexts.document_intelligence import (
    BatchStatus,
    CandidateProfile,
    DocumentIngestionTask,
    ExtractedSkillClaim,
    FileContentHash,
    ImpactMetric,
    PiiRedactionMap,
    ProjectComplexityRecord,
    ScreeningBatch,
    SeniorityEstimate,
    WorkExperienceEntry,
)
from talent_screener.domain.contexts.document_intelligence.matching import (
    CandidateTier,
    CompetencyAlignmentItem,
    EpistemicSkillGap,
    GapType,
    HybridMatchVector,
    RecencyDecayFactor,
    SemanticMatchResult,
)
from talent_screener.domain.contexts.evaluation_analytics import (
    AuthenticityIndex,
    CompetencyScorecardItem,
    CompositeRubricScore,
    ConfidenceInterval,
    EvaluationReport,
    HiringRecommendationBand,
    IntegrityAuditEntry,
    RecruiterOverrideAudit,
)
from talent_screener.domain.contexts.identity_governance import (
    ConsentRecord,
    ConsentScope,
    ConsentStatus,
    QuotaPolicy,
    RetentionPolicy,
    TenantAccount,
)
from talent_screener.domain.contexts.live_interview import (
    AdaptiveTurn,
    DifficultyLevel,
    IntegrityFlag,
    InterviewSession,
    PromptHash,
    ResponseLatencyMetric,
    SessionStatus,
    SocraticProbe,
    TelemetrySnapshot,
    TurnEvaluationScore,
)
from talent_screener.domain.contexts.recruitment import (
    CompetencyCriterion,
    HardSkillGatekeeperSet,
    JobRequisition,
    RequisitionStatus,
    SeniorityLevel,
    TierThresholdConfig,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import (
    ConfidenceScore,
    SHA256Hash,
    TenantId,
)
from talent_screener.infrastructure.messaging import InMemoryDomainEventBus
from talent_screener.infrastructure.persistence.relational import (
    DatabaseConnectionConfig,
    SqlCandidatePiiVaultStore,
    SqlCandidateProfileRepository,
    SqlConsentRecordRepository,
    SqlDatabaseManager,
    SqlDialect,
    SqlEvaluationReportRepository,
    SqlIdempotencyStoreAdapter,
    SqlInterviewSessionRepository,
    SqlJobRequisitionRepository,
    SqlScreeningBatchRepository,
    SqlSemanticMatchResultRepository,
    SqlTenantAccountRepository,
    SqlTransactionalUnitOfWork,
    get_ddl_statements,
)


def _sample_criterion(*, is_gatekeeper: bool = True, weight: float = 1.0) -> CompetencyCriterion:
    return CompetencyCriterion(
        name="Distributed Systems Architecture",
        canonical_skill_id="SKILL_DISTRIBUTED_SYSTEMS",
        weight=weight,
        target_depth=4,
        is_gatekeeper=is_gatekeeper,
        raw_importance=1.5,
        ideal_anchor_descriptor="Design quorum-replicated stateful services with backpressure.",
    )


def _sample_requisition(tenant_id: TenantId) -> JobRequisition:
    c1 = _sample_criterion(is_gatekeeper=True, weight=0.6)
    c2 = CompetencyCriterion(
        name="Python Async Concurrency",
        canonical_skill_id="LANG_PYTHON",
        weight=0.4,
        target_depth=4,
        is_gatekeeper=False,
        raw_importance=1.0,
    )
    return JobRequisition(
        tenant_id=tenant_id,
        title="Staff Distributed Systems Engineer",
        description="Lead resilient backend systems architecture across multi-region clusters.",
        seniority_level=SeniorityLevel.STAFF,
        criteria=(c1, c2),
        gatekeepers=HardSkillGatekeeperSet.from_skill_ids({"SKILL_DISTRIBUTED_SYSTEMS"}),
        tier_thresholds=TierThresholdConfig(89.0, 76.0, 60.0),
        interview_timebox_minutes=45,
    )


def _sample_candidate_profile(
    tenant_id: TenantId,
    *,
    vault_ref_id: UUID | None = None,
) -> CandidateProfile:
    v_ref = vault_ref_id or generate_uuid7()
    work_entry = WorkExperienceEntry(
        role_title="Staff Software Engineer",
        normalized_role="STAFF_ENGINEER",
        company_token="[COMP_1]",
        start_year=2021,
        start_month=3,
        is_current=True,
    )
    project = ProjectComplexityRecord(
        project_name="Distributed Event Bus",
        scale_tier="HIGH_THROUGHPUT_PRODUCTION",
        summary="Built multi-region transactional outbox handling 80k msgs/sec.",
        source_quote="Built multi-region transactional outbox handling 80k msgs/sec.",
        linked_work_entry_id=work_entry.id,
        impact_metrics=(
            ImpactMetric(
                metric_type="THROUGHPUT",
                numeric_value=80000.0,
                unit="msg/s",
                source_quote="handling 80k msgs/sec",
            ),
        ),
    )
    claim = ExtractedSkillClaim(
        canonical_skill_id="SKILL_DISTRIBUTED_SYSTEMS",
        raw_label="Distributed Systems",
        source_quote="Built multi-region transactional outbox handling 80k msgs/sec.",
        claimed_depth=5,
        applied_months=48,
        recency_months=0,
        confidence=ConfidenceScore(0.95),
    )
    return CandidateProfile(
        tenant_id=tenant_id,
        anonymized_token=generate_uuid7(),
        file_hash=FileContentHash(
            sha256=SHA256Hash.from_text("candidate-cv-bytes"),
            file_size_bytes=4096,
            mime_type="application/pdf",
        ),
        pii_redaction_map=PiiRedactionMap.from_mapping(
            vault_reference_id=v_ref,
            token_categories={"[PII_EMAIL_1]": "EMAIL"},
        ),
        work_experiences=(work_entry,),
        projects=(project,),
        skill_claims=(claim,),
        seniority_estimate=SeniorityEstimate(
            level="STAFF",
            verified_experience_months=72,
            confidence=ConfidenceScore(0.92),
        ),
    )


@pytest.mark.unit
def test_schema_ddl_initializes_all_tables_and_wal_mode(tmp_path: Path) -> None:
    pg_ddl = "\n".join(get_ddl_statements(SqlDialect.POSTGRESQL))
    assert "TIMESTAMPTZ" in pg_ddl
    assert "JSONB" in pg_ddl
    assert "BYTEA" in pg_ddl
    assert "interview_state_checkpoints" in pg_ddl
    assert "transactional_outbox" in pg_ddl

    db_file = tmp_path / "phase6_wal.db"
    db = SqlDatabaseManager(DatabaseConnectionConfig(dsn=str(db_file), enable_wal=True))
    try:
        tables = set(db.table_names())
        expected = {
            "tenants",
            "consent_records",
            "job_requisitions",
            "competency_criteria",
            "screening_batches",
            "document_ingestion_tasks",
            "candidate_profiles",
            "candidate_pii_vault",
            "semantic_match_results",
            "interview_sessions",
            "interview_turns",
            "evaluation_reports",
            "recruiter_override_audits",
            "transactional_outbox",
            "idempotency_records",
            "interview_state_checkpoints",
            "vector_sync_watermarks",
        }
        assert expected.issubset(tables)
        wal_row = db.fetchone("PRAGMA journal_mode")
        assert wal_row is not None and str(wal_row[0]).lower() == "wal"
    finally:
        db.close()


@pytest.mark.unit
def test_all_eight_sql_repositories_round_trip_and_tenant_isolation(
    tmp_path: Path,
) -> None:
    asyncio.run(_run_all_eight_sql_repositories(tmp_path))


async def _run_all_eight_sql_repositories(tmp_path: Path) -> None:
    db_file = tmp_path / "repos.db"
    db = SqlDatabaseManager(DatabaseConnectionConfig(dsn=str(db_file)))
    tenant_a = TenantId.generate()
    tenant_b = TenantId.generate()

    try:
        tenant_repo = SqlTenantAccountRepository(db)
        consent_repo = SqlConsentRecordRepository(db)
        req_repo = SqlJobRequisitionRepository(db)
        batch_repo = SqlScreeningBatchRepository(db)
        profile_repo = SqlCandidateProfileRepository(db)
        match_repo = SqlSemanticMatchResultRepository(db)
        session_repo = SqlInterviewSessionRepository(db)
        report_repo = SqlEvaluationReportRepository(db)

        # 1. TenantAccount
        account = TenantAccount(
            tenant_id=tenant_a,
            organization_slug="acme-robotics",
            display_name="Acme Robotics Inc.",
            quota_policy=QuotaPolicy(max_concurrent_batches=8, subscription_tier="ENTERPRISE"),
            retention_policy=RetentionPolicy(pii_retention_days=90, erasure_sla_hours=12),
        )
        account.register_batch_started(10)
        account.consume_interview_quota()
        tenant_repo.save(account)
        loaded_acc = tenant_repo.get_by_slug("acme-robotics")
        assert loaded_acc is not None
        assert loaded_acc.active_batches_count == 1
        assert loaded_acc.monthly_interviews_count == 1
        assert loaded_acc.quota_policy.max_concurrent_batches == 8
        assert tenant_repo.get_by_id(tenant_b) is None

        # 2. JobRequisition
        req = _sample_requisition(tenant_a)
        req.publish()
        req.register_active_screening_batch(generate_uuid7())
        req_repo.save(req)
        loaded_req = req_repo.get_by_id(tenant_a, req.id)
        assert loaded_req is not None
        assert loaded_req.status is RequisitionStatus.PUBLISHED
        assert len(loaded_req.criteria) == 2
        assert loaded_req.is_rubric_locked is True
        assert req_repo.list_by_tenant(
            tenant_a, status=RequisitionStatus.PUBLISHED
        ) == (loaded_req,)
        assert req_repo.get_by_id(tenant_b, req.id) is None
        assert req_repo.list_by_tenant(tenant_b) == ()

        # 3. ScreeningBatch
        task1 = DocumentIngestionTask(
            filename="alice.pdf",
            file_hash=FileContentHash(
                sha256=SHA256Hash.from_text("file-1"),
                file_size_bytes=2048,
                mime_type="application/pdf",
            ),
        )
        task2 = DocumentIngestionTask(
            filename="bob.pdf",
            file_hash=FileContentHash(
                sha256=SHA256Hash.from_text("file-2"),
                file_size_bytes=3072,
                mime_type="application/pdf",
            ),
        )
        batch = ScreeningBatch(
            tenant_id=tenant_a,
            requisition_id=req.id,
            batch_label="Q4-Backend",
            tasks=(task1, task2),
        )
        batch.queue()
        batch.start_processing()
        cand_id = generate_uuid7()
        batch.mark_task_succeeded(task1.id, cand_id)
        batch.mark_task_failed(task2.id, error_code="CORRUPT_PDF", error_message="Truncated EOF")
        batch_repo.save(batch)
        loaded_batch = batch_repo.get_by_id(tenant_a, batch.id)
        assert loaded_batch is not None
        assert loaded_batch.status is BatchStatus.PARTIALLY_FAILED
        assert loaded_batch.completed_count == 1 and loaded_batch.failed_count == 1
        assert batch_repo.get_by_id(tenant_b, batch.id) is None

        # 4. CandidateProfile + CQRS ReadModel
        profile = _sample_candidate_profile(tenant_a)
        profile_repo.save(profile)
        loaded_profile = profile_repo.get_by_id(tenant_a, profile.id)
        assert loaded_profile is not None
        assert loaded_profile.seniority_estimate.verified_experience_months == 72
        assert loaded_profile.projects[0].impact_metrics[0].numeric_value == pytest.approx(80000.0)
        read_dto = await profile_repo.get_profile_read_model(
            tenant_id=tenant_a, profile_id=profile.id
        )
        assert read_dto is not None and read_dto.seniority_level == "STAFF"
        assert profile_repo.get_by_id(tenant_b, profile.id) is None
        assert (
            await profile_repo.get_profile_read_model(tenant_id=tenant_b, profile_id=profile.id)
            is None
        )

        # 5. ConsentRecord
        consent = ConsentRecord(
            tenant_id=tenant_a,
            candidate_id=profile.id,
            granted_scopes=(ConsentScope.AI_SCREENING, ConsentScope.INTEGRITY_TELEMETRY),
        )
        consent.request_erasure(reason="User requested deletion")
        consent_repo.save(consent)
        loaded_consent = consent_repo.get_by_candidate_id(tenant_a, profile.id)
        assert (
            loaded_consent is not None
            and loaded_consent.status is ConsentStatus.ERASURE_REQUESTED
        )
        assert consent_repo.get_by_candidate_id(tenant_b, profile.id) is None

        # 6. SemanticMatchResult
        hv = HybridMatchVector(
            dense_similarity=0.92,
            sparse_bm25_score=0.85,
            experience_alignment=0.95,
            recency_decay=RecencyDecayFactor.from_half_life_years(3.0),
            recency_months_elapsed=3,
        )
        comp_id = req.criteria[0].id
        match_res = SemanticMatchResult(
            tenant_id=tenant_a,
            requisition_id=req.id,
            candidate_profile_id=profile.id,
            rubric_version=req.rubric_version,
            match_vector=hv,
            alignment_items=(
                CompetencyAlignmentItem(
                    competency_id=comp_id,
                    canonical_skill_id="SKILL_DISTRIBUTED_SYSTEMS",
                    weight=1.0,
                    match_vector=hv,
                    achieved_score=0.91,
                    evidence_excerpts=("Designed transactional outbox",),
                ),
            ),
            skill_gaps=(
                EpistemicSkillGap(
                    competency_id=req.criteria[1].id,
                    canonical_skill_id="LANG_PYTHON",
                    gap_type=GapType.UNVERIFIED_CLAIM,
                    probe_priority=0.72,
                    suspect_claim_summary="Verify Python asyncio internals",
                ),
            ),
            tier=CandidateTier.FAST_TRACK,
        )
        match_repo.save(match_res)
        loaded_match = match_repo.get_by_candidate_and_requisition(tenant_a, req.id, profile.id)
        assert loaded_match is not None and loaded_match.tier is CandidateTier.FAST_TRACK
        assert len(match_repo.list_by_requisition(tenant_a, req.id)) == 1
        assert match_repo.list_by_requisition(tenant_b, req.id) == ()

        # 7. InterviewSession (turns, probes, telemetry, flags)
        session = InterviewSession(
            tenant_id=tenant_a,
            requisition_id=req.id,
            candidate_profile_id=profile.id,
            rubric_version=req.rubric_version,
            competency_queue=(comp_id, req.criteria[1].id),
        )
        session.initialize()
        session.start()
        probe = SocraticProbe(
            competency_id=comp_id,
            probe_index=1,
            strategy="UNSTATED_TRADEOFF_PROBE",
            target_concept="Backpressure under partition",
            probe_prompt="How do you prevent head-of-line blocking under Kafka broker slowdowns?",
            prompt_hash=PromptHash.from_prompt_text("probe-1"),
        )
        turn1 = AdaptiveTurn(
            turn_number=1,
            competency_id=comp_id,
            difficulty=DifficultyLevel.L4,
            question_prompt="Describe outbox relay idempotency guarantees.",
            prompt_hash=PromptHash.from_prompt_text("q-1"),
            candidate_response="We track event_id unique constraints and watermarks per partition.",
            evaluation=TurnEvaluationScore(4.5, 4.4, 4.2, 4.6, 0.05),
            tokens_used=420,
            socratic_probe=probe,
            latency_metric=ResponseLatencyMetric(ttft_ms=250, total_duration_ms=4200),
            integrity_flags=(
                IntegrityFlag(
                    flag_code="FOCUS_LOSS",
                    severity="LOW",
                    turn_number=1,
                    confidence=ConfidenceScore(0.75),
                    evidence_summary="Brief tab switch < 2s",
                ),
            ),
        )
        session.record_turn(
            turn1,
            telemetry=TelemetrySnapshot(
                turn_number=1, focus_loss_count=1, focus_loss_duration_ms=1500
            ),
            next_difficulty=DifficultyLevel.L5,
        )
        session.begin_final_evaluation()
        session.complete()
        session_repo.save(session)
        loaded_session = session_repo.get_by_id(tenant_a, session.id)
        assert loaded_session is not None
        assert loaded_session.status is SessionStatus.COMPLETED
        assert len(loaded_session.turns) == 1
        assert loaded_session.turns[0].socratic_probe is not None
        assert loaded_session.probe_count_for(comp_id) == 1
        assert session_repo.get_by_id(tenant_b, session.id) is None

        # 8. EvaluationReport + Finalize + RecruiterOverrideAudit
        item = CompetencyScorecardItem(
            competency_id=comp_id,
            competency_name="Distributed Systems Architecture",
            weight=1.0,
            target_depth=4,
            achieved_depth=4.4,
            normalized_score=88.0,
            evidence_turn_numbers=(1,),
            evidence_quotes=("unique constraints and watermarks",),
        )
        auth = AuthenticityIndex(score=0.92, confidence_interval=ConfidenceInterval(0.87, 0.97))
        comp_score = CompositeRubricScore.from_components(
            technical_interview_score=88.0,
            cv_match_score=90.0,
            authenticity_index=auth,
        )
        report = EvaluationReport(
            tenant_id=tenant_a,
            interview_session_id=session.id,
            requisition_id=req.id,
            candidate_profile_id=profile.id,
            scorecard_items=(item,),
            composite_score=comp_score,
            authenticity_index=auth,
            recommendation_band=HiringRecommendationBand.HIRE,
            executive_summary="Strong systems depth with verifiable production metrics.",
            integrity_entries=(
                IntegrityAuditEntry(
                    turn_number=1,
                    flag_code="FOCUS_LOSS",
                    severity="LOW",
                    suspicion_risk=0.10,
                    description="Benign transient blur",
                ),
            ),
            valid_turn_numbers=frozenset({1}),
        )
        digest = report.finalize()
        report.record_recruiter_override(
            RecruiterOverrideAudit(
                actor_id=generate_uuid7(),
                previous_band=HiringRecommendationBand.HIRE,
                new_band=HiringRecommendationBand.STRONG_HIRE,
                justification="Executive panel unanimous endorsement on architecture tradeoffs.",
            )
        )
        report_repo.save(report)
        loaded_report = report_repo.get_by_session_id(tenant_a, session.id)
        assert loaded_report is not None
        assert loaded_report.is_finalized is True
        assert loaded_report.report_hash == digest
        assert loaded_report.effective_recommendation_band is HiringRecommendationBand.STRONG_HIRE
        assert len(loaded_report.override_audits) == 1
        assert report_repo.get_by_candidate_and_requisition(tenant_b, req.id, profile.id) is None

        # Cross-tenant invalid context rejection
        with pytest.raises(UnauthorizedTenantAccessError, match="MISSING_TENANT_CONTEXT"):
            req_repo.get_by_id("not-a-tenant-id", req.id)  # type: ignore[arg-type]

        # Simulate process restart with new SqlDatabaseManager on same WAL file
        db.close()
        restarted_db = SqlDatabaseManager(DatabaseConnectionConfig(dsn=str(db_file)))
        try:
            restarted_report = SqlEvaluationReportRepository(restarted_db).get_by_id(
                tenant_a, report.id
            )
            assert restarted_report is not None
            assert restarted_report.report_hash == digest
        finally:
            restarted_db.close()
    finally:
        if db.connection:
            with contextlib.suppress(Exception):
                db.close()


@pytest.mark.unit
def test_optimistic_concurrency_control_detects_stale_updates() -> None:
    db = SqlDatabaseManager(DatabaseConnectionConfig(dsn=":memory:"))
    try:
        tenant = TenantId.generate()
        repo1 = SqlJobRequisitionRepository(db)
        repo2 = SqlJobRequisitionRepository(db)

        req = _sample_requisition(tenant)
        repo1.save(req)

        worker1 = repo1.get_by_id(tenant, req.id)
        worker2 = repo2.get_by_id(tenant, req.id)
        assert worker1 is not None and worker2 is not None
        assert int(worker1.version) == 1 and int(worker2.version) == 1

        worker1.publish()
        repo1.save(worker1)
        assert int(worker1.version) == 2

        worker2.archive()
        with pytest.raises(ResourceConflictError) as exc_info:
            repo2.save(worker2)
        assert exc_info.value.code == "OPTIMISTIC_CONCURRENCY_CONFLICT"

        # Authoritative DB state remains worker1's published version=2
        current = repo1.get_by_id(tenant, req.id)
        assert current is not None
        assert current.status is RequisitionStatus.PUBLISHED
        assert int(current.version) == 2
    finally:
        db.close()


@pytest.mark.unit
def test_transactional_uow_atomic_outbox_and_rollback() -> None:
    asyncio.run(_run_transactional_uow_atomic_outbox_and_rollback())


async def _run_transactional_uow_atomic_outbox_and_rollback() -> None:
    db = SqlDatabaseManager(DatabaseConnectionConfig(dsn=":memory:"))
    bus = InMemoryDomainEventBus()
    uow = SqlTransactionalUnitOfWork(db=db, event_bus=bus)
    repo = SqlJobRequisitionRepository(db)
    tenant = TenantId.generate()

    try:
        req = _sample_requisition(tenant)
        async with uow:
            await uow.begin(tenant)
            repo.save(req)
            uow.register_aggregate(req)
            await uow.commit()

        # Rollback scenario: mutate & save inside UoW, then abort before commit
        loaded = repo.get_by_id(tenant, req.id)
        assert loaded is not None and loaded.status is RequisitionStatus.DRAFT

        with pytest.raises(RuntimeError, match="Simulated crash"):
            async with uow:
                await uow.begin(tenant)
                loaded.publish()
                repo.save(loaded)
                uow.register_aggregate(loaded)
                raise RuntimeError("Simulated crash prior to SQL COMMIT")

        assert len(bus.published_events) == 0
        outbox_before = db.fetchall(
            "SELECT * FROM transactional_outbox WHERE tenant_id = ?",
            (str(tenant.value),),
        )
        assert outbox_before == ()
        after_abort = repo.get_by_id(tenant, req.id)
        assert after_abort is not None
        assert after_abort.status is RequisitionStatus.DRAFT
        assert int(after_abort.version) == 1

        # Successful commit persists mutation + Outbox row atomically then publishes event
        async with uow:
            await uow.begin(tenant)
            after_abort.publish()
            repo.save(after_abort)
            uow.register_aggregate(after_abort)
            await uow.commit()

        assert uow.commit_count == 2
        assert len(bus.published_events) == 1
        outbox_rows = db.fetchall(
            "SELECT event_type, status, aggregate_id FROM transactional_outbox WHERE tenant_id = ?",
            (str(tenant.value),),
        )
        assert len(outbox_rows) == 1
        assert outbox_rows[0]["event_type"] == "JobRequisitionPublishedEvent"
        assert outbox_rows[0]["status"] == "PENDING"
        assert outbox_rows[0]["aggregate_id"] == str(req.id)
    finally:
        db.close()


@pytest.mark.unit
def test_sql_idempotency_store_and_pii_vault_persistence_and_shredding(
    tmp_path: Path,
) -> None:
    asyncio.run(_run_sql_idempotency_and_pii_vault(tmp_path))


async def _run_sql_idempotency_and_pii_vault(tmp_path: Path) -> None:
    db_path = tmp_path / "vault_idemp.db"
    db = SqlDatabaseManager(DatabaseConnectionConfig(dsn=str(db_path)))
    master_kek = b"K" * 32
    pepper = b"P" * 32
    tenant_a = TenantId.generate()
    tenant_b = TenantId.generate()
    cand_id = generate_uuid7()

    try:
        idem = SqlIdempotencyStoreAdapter(db)
        assert (
            await idem.check_and_reserve(
                tenant_id=tenant_a,
                operation="INITIATE_BATCH",
                idempotency_key="key-1",
                payload_hash="abc123",
            )
            is None
        )
        with pytest.raises(IdempotencyConflictError):
            await idem.check_and_reserve(
                tenant_id=tenant_a,
                operation="INITIATE_BATCH",
                idempotency_key="key-1",
                payload_hash="different-hash",
            )
        res_id = generate_uuid7()
        await idem.mark_completed(
            tenant_id=tenant_a,
            operation="INITIATE_BATCH",
            idempotency_key="key-1",
            resource_id=res_id,
            serialized_result={"batch_id": str(res_id)},
        )
        replayed = await idem.check_and_reserve(
            tenant_id=tenant_a,
            operation="INITIATE_BATCH",
            idempotency_key="key-1",
            payload_hash="abc123",
        )
        assert replayed is not None and replayed.resource_id == res_id

        # SQL PII Vault across restart + crypto-shredding
        vault1 = SqlCandidatePiiVaultStore(db, master_kek=master_kek, blind_index_pepper=pepper)
        outcome = await vault1.tokenize_and_vault(
            tenant_id=tenant_a,
            candidate_profile_id=cand_id,
            raw_text="Candidate Name: Jane Doe\nEmail: jane.doe@example.com",
        )
        assert "[PII_EMAIL_1]" in outcome.anonymized_text
        assert "jane.doe@example.com" not in outcome.anonymized_text

        with pytest.raises(UnauthorizedTenantAccessError, match="CROSS_TENANT_ACCESS_DENIED"):
            await vault1.detokenize(
                tenant_id=tenant_b, vault_reference_id=outcome.vault_reference_id
            )

        # Restart simulation reading from SQL table directly
        vault2 = SqlCandidatePiiVaultStore(db, master_kek=master_kek, blind_index_pepper=pepper)
        secrets = await vault2.detokenize(
            tenant_id=tenant_a, vault_reference_id=outcome.vault_reference_id
        )
        assert "jane.doe@example.com" in secrets.values()

        shred = await vault2.crypto_shred(
            tenant_id=tenant_a,
            candidate_id=cand_id,
            reason="GDPR Erasure Request",
        )
        assert shred.already_shredded is False
        audit = vault2.get_audit_snapshot(tenant_id=tenant_a, candidate_id=cand_id)
        assert audit is not None
        assert audit.is_shredded is True
        assert audit.has_wrapped_dek is False
        assert audit.has_ciphertext is False
        assert audit.blind_email_hmac_index is None

        with pytest.raises(ResourceConflictError, match="PII_CRYPTO_SHREDDED"):
            await vault2.detokenize(
                tenant_id=tenant_a, vault_reference_id=outcome.vault_reference_id
            )
        with pytest.raises(ResourceNotFoundError):
            await vault2.detokenize(tenant_id=tenant_a, vault_reference_id=generate_uuid7())
    finally:
        db.close()
