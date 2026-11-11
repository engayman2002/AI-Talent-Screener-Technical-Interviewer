"""End-to-end in-process Domain Event Choreography integration tests."""

from __future__ import annotations

import pytest

from talent_screener.application.document_intelligence.dtos import (
    BatchFileUploadSpecDTO,
    InitiateScreeningBatchRequestDTO,
    ProcessDocumentIngestionTaskRequestDTO,
)
from talent_screener.application.identity_governance.dtos import ProvisionTenantAccountRequestDTO
from talent_screener.application.live_interview.dtos import (
    ClientTelemetryPayloadDTO,
    CompleteInterviewSessionRequestDTO,
    InitializeInterviewSessionRequestDTO,
    ProcessCandidateTurnRequestDTO,
)
from talent_screener.application.recruitment.dtos import (
    CompetencyCriterionInputDTO,
    CreateJobRequisitionRequestDTO,
    GetJobRequisitionQueryDTO,
    PublishJobRequisitionRequestDTO,
    UpdateJobRubricRequestDTO,
)
from talent_screener.domain.contexts.document_intelligence import ScreeningBatchCreatedEvent
from talent_screener.domain.contexts.evaluation_analytics import (
    EvaluationReportGeneratedEvent,
)
from talent_screener.domain.contexts.live_interview import (
    InterviewSessionCompletedEvent,
    InterviewSessionStartedEvent,
    RoutingAction,
)
from talent_screener.domain.contexts.recruitment import (
    RubricLockedError,
    SeniorityLevel,
)
from talent_screener.domain.shared import SHA256Hash
from tests.unit.application.fakes import build_test_harness, run_async


@pytest.mark.unit
def test_end_to_end_cross_context_domain_event_choreography() -> None:
    """Verify UoW commit -> EventBus -> cross-context handlers automatically orchestrate."""

    async def _run() -> None:
        h = build_test_harness()
        c = h.container

        tenant = await c.provision_tenant_account.execute(
            ProvisionTenantAccountRequestDTO(
                organization_slug="choreo-ai",
                display_name="Choreo AI",
            )
        )
        tid = tenant.tenant_id

        req = await c.create_job_requisition.execute(
            CreateJobRequisitionRequestDTO(
                tenant_id=tid,
                title="Lead Distributed Systems Engineer",
                description="Lead event-driven hiring workflows.",
                seniority_level=SeniorityLevel.STAFF,
                criteria=(
                    CompetencyCriterionInputDTO(
                        name="Python",
                        canonical_skill_id="SKILL_LANG_PYTHON",
                        weight=1.0,
                        target_depth=4,
                        is_gatekeeper=True,
                    ),
                ),
            )
        )
        await c.publish_job_requisition.execute(
            PublishJobRequisitionRequestDTO(tenant_id=tid, requisition_id=req.requisition_id)
        )

        # 1. Initiating a ScreeningBatch emits ScreeningBatchCreatedEvent -> locks rubric!
        batch = await c.initiate_screening_batch.execute(
            InitiateScreeningBatchRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                batch_label="choreo-batch",
                files=(
                    BatchFileUploadSpecDTO(
                        "cv.pdf", SHA256Hash.from_bytes(b"choreo-cv").value, 2048, "application/pdf"
                    ),
                ),
                idempotency_key="choreo-idem-1",
                correlation_id="choreo-corr-1",
            )
        )
        read_req = await c.get_job_requisition.execute(
            GetJobRequisitionQueryDTO(tenant_id=tid, requisition_id=req.requisition_id)
        )
        assert read_req.is_rubric_locked

        with pytest.raises(RubricLockedError):
            await c.update_job_rubric.execute(
                UpdateJobRubricRequestDTO(
                    tenant_id=tid,
                    requisition_id=req.requisition_id,
                    criteria=(
                        CompetencyCriterionInputDTO(
                            name="Python",
                            canonical_skill_id="SKILL_LANG_PYTHON",
                            weight=1.0,
                            target_depth=5,
                            is_gatekeeper=True,
                        ),
                    ),
                )
            )

        # 2. Ingest CV -> Initialize Interview (emits InterviewSessionStartedEvent)
        instr = batch.upload_instructions[0]
        task = await c.process_document_task.execute(
            ProcessDocumentIngestionTaskRequestDTO(
                tenant_id=tid,
                batch_id=batch.batch_id,
                task_id=instr.task_id,
                object_key=instr.object_key,
                correlation_id="choreo-corr-1",
            )
        )
        assert task.candidate_profile_id is not None

        session = await c.initialize_interview_session.execute(
            InitializeInterviewSessionRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                candidate_profile_id=task.candidate_profile_id,
            )
        )
        h.interview_graph.next_routing_action = RoutingAction.ESCALATE_DIFFICULTY
        await c.process_candidate_turn.execute(
            ProcessCandidateTurnRequestDTO(
                tenant_id=tid,
                session_id=session.session_id,
                turn_number=1,
                checkpoint_version=session.checkpoint_version,
                candidate_utterance="Used asyncio queues with backpressure.",
                remaining_seconds=1700,
                telemetry=ClientTelemetryPayloadDTO(0, 0, False, 0, False, 1200, 130.0, 0.5),
                correlation_id="choreo-t1",
            )
        )

        # 3. Completing Interview emits InterviewSessionCompletedEvent -> auto-generates Report!
        await c.complete_interview_session.execute(
            CompleteInterviewSessionRequestDTO(
                tenant_id=tid,
                session_id=session.session_id,
                checkpoint_version="ckpt-1",
            )
        )

        auto_report = c.repositories.report_repo.get_by_session_id(tid, session.session_id)
        assert auto_report is not None
        assert auto_report.is_finalized

        bus = c.event_bus
        published = getattr(bus, "published_events", ())
        assert any(isinstance(e, ScreeningBatchCreatedEvent) for e in published)
        assert any(isinstance(e, InterviewSessionStartedEvent) for e in published)
        assert any(isinstance(e, InterviewSessionCompletedEvent) for e in published)
        assert any(isinstance(e, EvaluationReportGeneratedEvent) for e in published)

    run_async(_run())
