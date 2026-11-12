"""End-to-end integration tests for Phase 6 ACID Persistence, Outbox, Reconciler & Checkpointer."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from uuid import UUID

import pytest

from talent_screener.application.common.exceptions import ResourceConflictError
from talent_screener.application.document_intelligence.dtos import (
    BatchFileUploadSpecDTO,
    ExecuteHybridMatchingRequestDTO,
    InitiateScreeningBatchRequestDTO,
    ProcessDocumentIngestionTaskRequestDTO,
)
from talent_screener.application.evaluation_analytics.dtos import (
    RecordRecruiterOverrideRequestDTO,
)
from talent_screener.application.identity_governance.dtos import (
    ExecuteCandidateErasureRequestDTO,
    ProvisionTenantAccountRequestDTO,
    RecordCandidateConsentRequestDTO,
)
from talent_screener.application.live_interview.dtos import (
    ClientTelemetryPayloadDTO,
    CompleteInterviewSessionRequestDTO,
    InitializeInterviewSessionRequestDTO,
    ProcessCandidateTurnRequestDTO,
)
from talent_screener.application.recruitment.dtos import (
    CompetencyCriterionInputDTO,
    CreateJobRequisitionRequestDTO,
    PublishJobRequisitionRequestDTO,
)
from talent_screener.composition_root import build_phase6_container
from talent_screener.domain.contexts.document_intelligence import TaskIngestionStatus
from talent_screener.domain.contexts.document_intelligence.value_objects import BatchStatus
from talent_screener.domain.contexts.evaluation_analytics import HiringRecommendationBand
from talent_screener.domain.contexts.identity_governance import ConsentScope, ConsentStatus
from talent_screener.domain.contexts.live_interview import SessionStatus
from talent_screener.domain.contexts.recruitment import SeniorityLevel
from talent_screener.domain.shared import SHA256Hash, TenantId
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.infrastructure.persistence.vector_store import OutboxStatus


def _clean_telemetry(latency_ms: int = 1250) -> ClientTelemetryPayloadDTO:
    return ClientTelemetryPayloadDTO(
        focus_loss_count=0,
        focus_loss_duration_ms=0,
        external_paste_detected=False,
        paste_char_count=0,
        keystroke_anomaly_detected=False,
        response_latency_ms=latency_ms,
        speech_cadence_wpm=136.0,
        pause_entropy=0.22,
    )


@pytest.mark.integration
def test_phase6_end_to_end_persistence_restart_checkpoint_and_erasure(
    tmp_path: Path,
) -> None:
    asyncio.run(_run_phase6_lifecycle(tmp_path))


async def _run_phase6_lifecycle(tmp_path: Path) -> None:
    db_path = tmp_path / "phase6_lifecycle.db"
    master_kek = b"Z" * 32

    bundle1 = build_phase6_container(
        dsn=str(db_path),
        enable_wal=True,
        master_kek=master_kek,
    )
    tenant_b = TenantId.generate()

    try:
        c1 = bundle1.container
        # 1. Provision Tenant & JobRequisition
        tenant_res = await c1.provision_tenant_account.execute(
            ProvisionTenantAccountRequestDTO(
                organization_slug="hyperion-ai",
                display_name="Hyperion Systems Ltd.",
                max_files_per_batch=50,
            )
        )
        tid = tenant_res.tenant_id

        req_res = await c1.create_job_requisition.execute(
            CreateJobRequisitionRequestDTO(
                tenant_id=tid,
                title="Staff Distributed Infrastructure Engineer",
                description=(
                    "Architect Python asyncio ingestion workers, transactional outbox pipelines, "
                    "and Raft consensus metadata clusters."
                ),
                seniority_level=SeniorityLevel.STAFF,
                criteria=(
                    CompetencyCriterionInputDTO(
                        name="Python & Distributed Storage",
                        canonical_skill_id="SKILL_LANG_PYTHON",
                        weight=0.55,
                        target_depth=4,
                        is_gatekeeper=True,
                        min_applied_months=24,
                        ideal_anchor_descriptor=(
                            "ACID transactional outbox, WAL persistence, asyncio backpressure"
                        ),
                    ),
                    CompetencyCriterionInputDTO(
                        name="Consensus & Fencing Systems",
                        canonical_skill_id="SKILL_DISTRIBUTED_SYSTEMS",
                        weight=0.45,
                        target_depth=4,
                        is_gatekeeper=False,
                        ideal_anchor_descriptor=(
                            "Raft leader leases, monotonic fencing tokens, split-brain recovery"
                        ),
                    ),
                ),
            )
        )
        await c1.publish_job_requisition.execute(
            PublishJobRequisitionRequestDTO(tenant_id=tid, requisition_id=req_res.requisition_id)
        )

        # 2. Idempotent ScreeningBatch initiation & replay via SQL IdempotencyStore
        cv_bytes = (
            b"%PDF-1.7\n"
            b"Name: Jordan Rivera\n"
            b"Email: jordan.rivera@example.com\n"
            b"SUMMARY\n"
            b"Staff backend engineer specializing in Python asyncio and distributed state.\n"
            b"EXPERIENCE\n"
            b"Staff Infrastructure Engineer 2019-02 to Present\n"
            b"Designed Python asyncio transactional outbox relays over SQLite WAL and PostgreSQL "
            b"handling 75k events/sec with Raft fencing tokens and zero split-brain corruption.\n"
        )
        batch_req = InitiateScreeningBatchRequestDTO(
            tenant_id=tid,
            requisition_id=req_res.requisition_id,
            batch_label="batch-phase6",
            files=(
                BatchFileUploadSpecDTO(
                    filename="jordan.pdf",
                    sha256_hex=SHA256Hash.from_bytes(cv_bytes).value,
                    file_size_bytes=len(cv_bytes),
                    mime_type="application/pdf",
                ),
            ),
            idempotency_key="phase6-idem-key-1",
            correlation_id="corr-p6-1",
        )
        batch_init = await c1.initiate_screening_batch.execute(batch_req)
        batch_replay = await c1.initiate_screening_batch.execute(batch_req)
        assert batch_replay.replayed_from_idempotency is True
        assert batch_replay.batch_id == batch_init.batch_id

        # 3. Document Ingestion -> SQL PII Vault + CandidateProfile + Outbox Sync
        instr = batch_init.upload_instructions[0]
        await bundle1.adapters.object_storage.put_bytes(
            tenant_id=tid,
            object_key=instr.object_key,
            payload=cv_bytes,
            content_type="application/pdf",
        )
        ingested = await c1.process_document_task.execute(
            ProcessDocumentIngestionTaskRequestDTO(
                tenant_id=tid,
                batch_id=batch_init.batch_id,
                task_id=instr.task_id,
                object_key=instr.object_key,
                correlation_id="corr-ingest-p6",
            )
        )
        assert ingested.task_status is TaskIngestionStatus.COMPLETED
        assert ingested.batch_status is BatchStatus.COMPLETED
        assert ingested.candidate_profile_id is not None
        cand_id: UUID = ingested.candidate_profile_id

        # Outbox relay processes CandidateProfileNormalizedEvent & marks outbox PUBLISHED
        relay_receipt = await bundle1.outbox_relay.poll_and_dispatch(tenant_id=tid)
        assert relay_receipt.published_count >= 1
        assert (
            bundle1.outbox_relay.list_records(tenant_id=tid, status=OutboxStatus.PENDING)
            == ()
        )

        # Grant GDPR consent
        await c1.record_candidate_consent.execute(
            RecordCandidateConsentRequestDTO(
                tenant_id=tid,
                candidate_id=cand_id,
                granted_scopes=(
                    ConsentScope.AI_SCREENING,
                    ConsentScope.VOICE_INTERVIEW_RECORDING,
                    ConsentScope.INTEGRITY_TELEMETRY,
                ),
            )
        )

        # Close bundle1 DB -> Simulate full process restart from SQLite WAL file!
        bundle1.db_manager.close()

        bundle2 = build_phase6_container(
            dsn=str(db_path),
            enable_wal=True,
            master_kek=master_kek,
        )
        try:
            c2 = bundle2.container
            # Reconciler rebuilds INT8 vector index from durable SQLite after cold restart
            drift = await bundle2.reconciler.reconcile_tenant(
                tid, default_requisition_id=req_res.requisition_id
            )
            assert drift.missing_healed == 1

            # Verify Vault secret decryption survived SQL restart
            loaded_prof = bundle2.repositories.profile_repo.get_by_id(tid, cand_id)
            assert loaded_prof is not None
            vault_secrets = await bundle2.pii_vault.detokenize(
                tenant_id=tid,
                vault_reference_id=loaded_prof.pii_redaction_map.vault_reference_id,
            )
            assert "jordan.rivera@example.com" in vault_secrets.values()

            # Tenant B isolation on cold restarted DB
            assert bundle2.repositories.profile_repo.get_by_id(tenant_b, cand_id) is None

            # 4. Hybrid Matching persisted into semantic_match_results
            match_res = await c2.execute_hybrid_matching.execute(
                ExecuteHybridMatchingRequestDTO(
                    tenant_id=tid,
                    requisition_id=req_res.requisition_id,
                    candidate_profile_id=cand_id,
                )
            )
            assert match_res.composite_score > 0.0
            persisted_match = bundle2.repositories.match_repo.get_by_id(
                tid, match_res.match_result_id
            )
            assert persisted_match is not None

            # 5. Live Adaptive Interview + Two-Tier Checkpointer L1 Flush Recovery
            init_iv = await c2.initialize_interview_session.execute(
                InitializeInterviewSessionRequestDTO(
                    tenant_id=tid,
                    requisition_id=req_res.requisition_id,
                    candidate_profile_id=cand_id,
                    max_turns=6,
                )
            )
            # Evict L1 checkpointer cache to prove seamless resume from L2 SQLite
            bundle2.checkpointer.invalidate_l1(tid, init_iv.session_id)

            turn1 = await c2.process_candidate_turn.execute(
                ProcessCandidateTurnRequestDTO(
                    tenant_id=tid,
                    session_id=init_iv.session_id,
                    turn_number=1,
                    checkpoint_version=init_iv.checkpoint_version,
                    candidate_utterance=(
                        "We write domain events into transactional_outbox within the same SQL "
                        "transaction before commit, and use monotonic fencing tokens on worker "
                        "leases to prevent stale writes from split-brain processes."
                    ),
                    remaining_seconds=1500,
                    telemetry=_clean_telemetry(1100),
                    correlation_id="corr-turn-1",
                )
            )
            assert turn1.turn_number == 1
            assert bundle2.checkpointer.l2_hit_count >= 1

            # Evict L1 again and complete interview cleanly via L2 SQLite fallback
            bundle2.checkpointer.invalidate_l1(tid, init_iv.session_id)
            completed = await c2.complete_interview_session.execute(
                CompleteInterviewSessionRequestDTO(
                    tenant_id=tid,
                    session_id=init_iv.session_id,
                    checkpoint_version=turn1.updated_checkpoint_version,
                )
            )
            assert completed.status is SessionStatus.COMPLETED

            # Post-commit event choreography automatically generated & finalized EvaluationReport!
            persisted_report = bundle2.repositories.report_repo.get_by_session_id(
                tid, init_iv.session_id
            )
            assert persisted_report is not None and persisted_report.is_finalized is True

            next_band = (
                HiringRecommendationBand.HIRE
                if persisted_report.effective_recommendation_band
                is not HiringRecommendationBand.HIRE
                else HiringRecommendationBand.STRONG_HIRE
            )
            overridden = await c2.record_recruiter_override.execute(
                RecordRecruiterOverrideRequestDTO(
                    tenant_id=tid,
                    report_id=persisted_report.id,
                    actor_id=generate_uuid7(),
                    new_band=next_band,
                    justification="Panel confirmed strong WAL and fencing-token understanding.",
                )
            )
            assert overridden.effective_recommendation_band is next_band
            assert overridden.override_count == 1

            reloaded_report = bundle2.repositories.report_repo.get_by_id(
                tid, persisted_report.id
            )
            assert reloaded_report is not None and len(reloaded_report.override_audits) == 1

            # Checkpoints recorded across init -> turn1 -> finalize
            ckpt_history = bundle2.checkpointer.list_history(tid, init_iv.session_id)
            assert [c.fencing_token for c in ckpt_history] == [1, 2, 3]

            # 6. GDPR Article 17 Erasure + Outbox + Anti-Entropy Verification
            erasure = await c2.execute_candidate_erasure.execute(
                ExecuteCandidateErasureRequestDTO(
                    tenant_id=tid,
                    candidate_id=cand_id,
                    reason="Candidate exercised Right to Erasure",
                )
            )
            assert erasure.status is ConsentStatus.CRYPTO_SHREDDED
            assert erasure.vectors_purged is True

            await bundle2.outbox_relay.poll_and_dispatch(tenant_id=tid)
            post_erase_drift = await bundle2.reconciler.reconcile_tenant(
                tid, default_requisition_id=req_res.requisition_id
            )
            assert post_erase_drift.total_drift_repaired == 0

            # Detokenization permanently fails even after another DB reload
            with pytest.raises(ResourceConflictError, match="PII_CRYPTO_SHREDDED"):
                await bundle2.pii_vault.detokenize(
                    tenant_id=tid,
                    vault_reference_id=loaded_prof.pii_redaction_map.vault_reference_id,
                )
            assert (
                bundle2.quantized_vector_backend.get_sync_version(
                    tenant_id=tid, candidate_profile_id=cand_id
                )
                is None
            )
        finally:
            bundle2.db_manager.close()
    finally:
        with contextlib.suppress(Exception):
            bundle1.db_manager.close()
