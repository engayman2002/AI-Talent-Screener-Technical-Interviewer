"""Unit tests for Evaluation & Analytics and Identity & Governance Use Cases."""

from __future__ import annotations

from uuid import UUID

import pytest

from talent_screener.application.common.exceptions import (
    ResourceConflictError,
    ResourceNotFoundError,
)
from talent_screener.application.document_intelligence.dtos import (
    BatchFileUploadSpecDTO,
    ExecuteHybridMatchingRequestDTO,
    InitiateScreeningBatchRequestDTO,
    ProcessDocumentIngestionTaskRequestDTO,
)
from talent_screener.application.evaluation_analytics.dtos import (
    BuildCandidateComparisonMatrixRequestDTO,
    GenerateEvaluationReportRequestDTO,
    RecordRecruiterOverrideRequestDTO,
)
from talent_screener.application.identity_governance.dtos import (
    ExecuteCandidateErasureRequestDTO,
    ProvisionTenantAccountRequestDTO,
    RecordCandidateConsentRequestDTO,
    RevokeCandidateConsentRequestDTO,
)
from talent_screener.application.live_interview.dtos import (
    ClientTelemetryPayloadDTO,
    InitializeInterviewSessionRequestDTO,
    ProcessCandidateTurnRequestDTO,
)
from talent_screener.application.recruitment.dtos import (
    CompetencyCriterionInputDTO,
    CreateJobRequisitionRequestDTO,
    PublishJobRequisitionRequestDTO,
)
from talent_screener.domain.contexts.evaluation_analytics import HiringRecommendationBand
from talent_screener.domain.contexts.identity_governance import ConsentScope, ConsentStatus
from talent_screener.domain.contexts.live_interview import (
    DifficultyLevel,
    RoutingAction,
    TurnEvaluationScore,
)
from talent_screener.domain.contexts.recruitment import SeniorityLevel
from talent_screener.domain.shared import SHA256Hash, TenantId, generate_uuid7
from tests.unit.application.fakes import ApplicationTestHarness, build_test_harness, run_async


async def _run_candidate_through_interview(
    h: ApplicationTestHarness,
    *,
    tid: TenantId,
    req_id: UUID,
    filename: str,
    seed: bytes,
    turn_score: TurnEvaluationScore,
    paste_flag: bool = False,
) -> tuple[UUID, UUID, UUID]:
    batch = await h.container.initiate_screening_batch.execute(
        InitiateScreeningBatchRequestDTO(
            tenant_id=tid,
            requisition_id=req_id,
            batch_label=f"batch-{filename}",
            files=(
                BatchFileUploadSpecDTO(
                    filename, SHA256Hash.from_bytes(seed).value, 2048, "application/pdf"
                ),
            ),
            idempotency_key=f"idem-{filename}",
            correlation_id=f"corr-{filename}",
        )
    )
    instr = batch.upload_instructions[0]
    task = await h.container.process_document_task.execute(
        ProcessDocumentIngestionTaskRequestDTO(
            tenant_id=tid,
            batch_id=batch.batch_id,
            task_id=instr.task_id,
            object_key=instr.object_key,
            correlation_id=f"corr-{filename}",
        )
    )
    assert task.candidate_profile_id is not None
    cand_id = task.candidate_profile_id
    await h.container.record_candidate_consent.execute(
        RecordCandidateConsentRequestDTO(
            tenant_id=tid,
            candidate_id=cand_id,
            granted_scopes=(ConsentScope.AI_SCREENING, ConsentScope.INTEGRITY_TELEMETRY),
        )
    )
    await h.container.execute_hybrid_matching.execute(
        ExecuteHybridMatchingRequestDTO(
            tenant_id=tid,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
        )
    )

    init_res = await h.container.initialize_interview_session.execute(
        InitializeInterviewSessionRequestDTO(
            tenant_id=tid,
            requisition_id=req_id,
            candidate_profile_id=cand_id,
            initial_difficulty=DifficultyLevel.L5,
        )
    )
    h.interview_graph.next_score = turn_score
    h.interview_graph.next_routing_action = RoutingAction.TRANSITION_NEXT_COMPETENCY
    h.interview_graph.next_difficulty = DifficultyLevel.L5
    t1 = await h.container.process_candidate_turn.execute(
        ProcessCandidateTurnRequestDTO(
            tenant_id=tid,
            session_id=init_res.session_id,
            turn_number=1,
            checkpoint_version=init_res.checkpoint_version,
            candidate_utterance="Detailed architecture response.",
            remaining_seconds=1600,
            telemetry=ClientTelemetryPayloadDTO(
                focus_loss_count=3 if paste_flag else 0,
                focus_loss_duration_ms=7000 if paste_flag else 0,
                external_paste_detected=paste_flag,
                paste_char_count=300 if paste_flag else 0,
                keystroke_anomaly_detected=paste_flag,
                response_latency_ms=11000 if paste_flag else 1600,
                speech_cadence_wpm=180.0 if paste_flag else 130.0,
                pause_entropy=0.1 if paste_flag else 0.7,
            ),
            correlation_id=f"turn1-{filename}",
        )
    )
    h.interview_graph.next_routing_action = RoutingAction.CONCLUDE_INTERVIEW
    await h.container.process_candidate_turn.execute(
        ProcessCandidateTurnRequestDTO(
            tenant_id=tid,
            session_id=init_res.session_id,
            turn_number=2,
            checkpoint_version=t1.updated_checkpoint_version,
            candidate_utterance="Distributed partitioning details.",
            remaining_seconds=1500,
            telemetry=ClientTelemetryPayloadDTO(
                focus_loss_count=0,
                focus_loss_duration_ms=0,
                external_paste_detected=False,
                paste_char_count=0,
                keystroke_anomaly_detected=False,
                response_latency_ms=1400,
                speech_cadence_wpm=130.0,
                pause_entropy=0.7,
            ),
            correlation_id=f"turn2-{filename}",
        )
    )
    report = h.container.repositories.report_repo.get_by_session_id(tid, init_res.session_id)
    assert report is not None
    return cand_id, init_res.session_id, report.id


@pytest.mark.unit
def test_evaluation_report_override_and_comparison_matrix() -> None:
    async def _run() -> None:
        h = build_test_harness()
        tenant = await h.container.provision_tenant_account.execute(
            ProvisionTenantAccountRequestDTO(
                organization_slug="matrix-corp",
                display_name="Matrix Corp",
            )
        )
        tid = tenant.tenant_id
        req = await h.container.create_job_requisition.execute(
            CreateJobRequisitionRequestDTO(
                tenant_id=tid,
                title="Principal Platform Architect",
                description="Architect enterprise multi-tenant talent intelligence pipelines.",
                seniority_level=SeniorityLevel.PRINCIPAL,
                criteria=(
                    CompetencyCriterionInputDTO(
                        name="Python Async",
                        canonical_skill_id="SKILL_LANG_PYTHON",
                        weight=0.50,
                        target_depth=4,
                        is_gatekeeper=True,
                    ),
                    CompetencyCriterionInputDTO(
                        name="Distributed Systems",
                        canonical_skill_id="SKILL_DISTRIBUTED_SYSTEMS",
                        weight=0.50,
                        target_depth=4,
                        is_gatekeeper=False,
                    ),
                ),
            )
        )
        await h.container.publish_job_requisition.execute(
            PublishJobRequisitionRequestDTO(tenant_id=tid, requisition_id=req.requisition_id)
        )

        # Candidate A: authentic strong candidate
        cand_a, sess_a, rep_a = await _run_candidate_through_interview(
            h,
            tid=tid,
            req_id=req.requisition_id,
            filename="alice.pdf",
            seed=b"alice-1",
            turn_score=TurnEvaluationScore(4.8, 4.7, 4.6, 4.7, 0.02),
            paste_flag=False,
        )
        # Candidate B: high nominal answers but suspicious paste/cadence penalty
        cand_b, _sess_b, rep_b = await _run_candidate_through_interview(
            h,
            tid=tid,
            req_id=req.requisition_id,
            filename="bob.pdf",
            seed=b"bob-1",
            turn_score=TurnEvaluationScore(4.8, 4.7, 4.6, 4.7, 0.85),
            paste_flag=True,
        )

        # Explicit GenerateEvaluationReportUseCase call is idempotent on already-synthesized session
        idempotent_rep = await h.container.generate_evaluation_report.execute(
            GenerateEvaluationReportRequestDTO(tenant_id=tid, interview_session_id=sess_a)
        )
        assert idempotent_rep.report_id == rep_a
        assert idempotent_rep.is_finalized
        assert idempotent_rep.report_hash is not None

        # Record human recruiter override on Candidate B
        overridden = await h.container.record_recruiter_override.execute(
            RecordRecruiterOverrideRequestDTO(
                tenant_id=tid,
                report_id=rep_b,
                actor_id=generate_uuid7(),
                new_band=HiringRecommendationBand.NO_HIRE,
                justification="Candidate confirmed pasting generated answers during audit review.",
            )
        )
        assert overridden.effective_recommendation_band is HiringRecommendationBand.NO_HIRE
        assert overridden.override_count == 1

        # Side-by-Side Comparison Matrix ranks Candidate A above Integrity-flagged Candidate B
        matrix = await h.container.build_comparison_matrix.execute(
            BuildCandidateComparisonMatrixRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                candidate_profile_ids=(cand_b, cand_a),
            )
        )
        assert len(matrix.candidates) == 2
        assert matrix.candidates[0].candidate_profile_id == cand_a
        assert matrix.candidates[0].rank == 1
        assert matrix.candidates[1].candidate_profile_id == cand_b
        assert matrix.candidates[1].authenticity_score < matrix.candidates[0].authenticity_score
        assert any(cell.is_dimension_leader for cell in matrix.dimension_cells)

        # Invalid cardinality (<2 or >4) rejected
        with pytest.raises(ResourceConflictError):
            await h.container.build_comparison_matrix.execute(
                BuildCandidateComparisonMatrixRequestDTO(
                    tenant_id=tid,
                    requisition_id=req.requisition_id,
                    candidate_profile_ids=(cand_a,),
                )
            )

    run_async(_run())


@pytest.mark.unit
def test_identity_governance_tenant_consent_and_gdpr_erasure_use_cases() -> None:
    async def _run() -> None:
        h = build_test_harness()
        tenant = await h.container.provision_tenant_account.execute(
            ProvisionTenantAccountRequestDTO(
                organization_slug="gdpr-enterprise",
                display_name="GDPR Enterprise",
                pii_retention_days=60,
            )
        )
        assert tenant.pii_retention_days == 60

        # Duplicate slug rejected
        with pytest.raises(ResourceConflictError):
            await h.container.provision_tenant_account.execute(
                ProvisionTenantAccountRequestDTO(
                    organization_slug="gdpr-enterprise",
                    display_name="Duplicate",
                )
            )

        cand_id = generate_uuid7()
        consent = await h.container.record_candidate_consent.execute(
            RecordCandidateConsentRequestDTO(
                tenant_id=tenant.tenant_id,
                candidate_id=cand_id,
                granted_scopes=(ConsentScope.AI_SCREENING,),
            )
        )
        assert consent.status is ConsentStatus.GRANTED

        # Index dummy vectors to confirm purge upon erasure
        await h.vector_search.index_candidate_dossier(
            tenant_id=tenant.tenant_id,
            requisition_id=generate_uuid7(),
            candidate_profile_id=cand_id,
            dossier=await h.document_parser.parse_and_extract(
                command=None  # type: ignore[arg-type]
            ),
            sync_version=1,
        )
        assert (tenant.tenant_id.value, cand_id) in h.vector_search.indexed_profiles

        revoked = await h.container.revoke_candidate_consent.execute(
            RevokeCandidateConsentRequestDTO(
                tenant_id=tenant.tenant_id, record_id=consent.record_id
            )
        )
        assert revoked.status is ConsentStatus.WITHDRAWN

        erasure = await h.container.execute_candidate_erasure.execute(
            ExecuteCandidateErasureRequestDTO(
                tenant_id=tenant.tenant_id,
                candidate_id=cand_id,
                reason="Candidate GDPR Art. 17 erasure request",
            )
        )
        assert erasure.status is ConsentStatus.CRYPTO_SHREDDED
        assert erasure.vectors_purged
        assert (tenant.tenant_id.value, cand_id) not in h.vector_search.indexed_profiles
        assert len(h.pii_vault.shredded_candidates) == 1

        with pytest.raises(ResourceNotFoundError):
            await h.container.execute_candidate_erasure.execute(
                ExecuteCandidateErasureRequestDTO(
                    tenant_id=tenant.tenant_id,
                    candidate_id=generate_uuid7(),
                )
            )

    run_async(_run())
