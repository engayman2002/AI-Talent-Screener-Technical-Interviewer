"""Unit tests for Document Intelligence & Semantic Matching Use Cases."""

from __future__ import annotations

import pytest

from talent_screener.application.common.exceptions import (
    IdempotencyConflictError,
    QuotaExceededError,
    ResourceConflictError,
    UnauthorizedTenantAccessError,
)
from talent_screener.application.document_intelligence.dtos import (
    BatchFileUploadSpecDTO,
    ExecuteHybridMatchingRequestDTO,
    GetScreeningBatchStatusQueryDTO,
    InitiateScreeningBatchRequestDTO,
    ProcessDocumentIngestionTaskRequestDTO,
)
from talent_screener.application.identity_governance.dtos import ProvisionTenantAccountRequestDTO
from talent_screener.application.recruitment.dtos import (
    CompetencyCriterionInputDTO,
    CreateJobRequisitionRequestDTO,
    PublishJobRequisitionRequestDTO,
)
from talent_screener.domain.contexts.document_intelligence import (
    BatchStatus,
    CandidateTier,
    TaskIngestionStatus,
)
from talent_screener.domain.contexts.recruitment import SeniorityLevel
from talent_screener.domain.shared import SHA256Hash, TenantId, generate_uuid7
from tests.unit.application.fakes import ApplicationTestHarness, build_test_harness, run_async


async def _setup_published_req(
    h: ApplicationTestHarness, *, max_files_per_batch: int = 500
) -> tuple[TenantId, object]:
    tenant = await h.container.provision_tenant_account.execute(
        ProvisionTenantAccountRequestDTO(
            organization_slug="tech-corp",
            display_name="Tech Corp",
            max_files_per_batch=max_files_per_batch,
        )
    )
    req = await h.container.create_job_requisition.execute(
        CreateJobRequisitionRequestDTO(
            tenant_id=tenant.tenant_id,
            title="Senior Python Platform Engineer",
            description="Build asynchronous CV screening and semantic matching engines.",
            seniority_level=SeniorityLevel.SENIOR,
            criteria=(
                CompetencyCriterionInputDTO(
                    name="Python Async",
                    canonical_skill_id="SKILL_LANG_PYTHON",
                    weight=0.60,
                    target_depth=4,
                    is_gatekeeper=True,
                    min_applied_months=36,
                ),
                CompetencyCriterionInputDTO(
                    name="Distributed Systems",
                    canonical_skill_id="SKILL_DISTRIBUTED_SYSTEMS",
                    weight=0.40,
                    target_depth=4,
                    is_gatekeeper=False,
                ),
            ),
        )
    )
    await h.container.publish_job_requisition.execute(
        PublishJobRequisitionRequestDTO(
            tenant_id=tenant.tenant_id, requisition_id=req.requisition_id
        )
    )
    return tenant.tenant_id, req.requisition_id


@pytest.mark.unit
def test_initiate_batch_idempotency_quota_and_ingestion_pipeline() -> None:
    async def _run() -> None:
        h = build_test_harness()
        tid, req_id = await _setup_published_req(h, max_files_per_batch=2)
        assert isinstance(req_id, type(generate_uuid7()))

        file1 = BatchFileUploadSpecDTO(
            filename="alice.pdf",
            sha256_hex=SHA256Hash.from_bytes(b"alice-cv").value,
            file_size_bytes=4096,
            mime_type="application/pdf",
        )
        req_dto = InitiateScreeningBatchRequestDTO(
            tenant_id=tid,
            requisition_id=req_id,
            batch_label="cohort-1",
            files=(file1,),
            idempotency_key="idem-001",
            correlation_id="corr-001",
        )
        res = await h.container.initiate_screening_batch.execute(req_dto)
        assert not res.replayed_from_idempotency
        assert res.total_files == 1
        assert len(h.task_dispatcher.dispatched_tasks) == 1

        # Idempotent replay returns same batch_id without re-dispatching
        replay = await h.container.initiate_screening_batch.execute(req_dto)
        assert replay.replayed_from_idempotency
        assert replay.batch_id == res.batch_id
        assert len(h.task_dispatcher.dispatched_tasks) == 1

        # Idempotency conflict with different payload raises IdempotencyConflictError
        with pytest.raises(IdempotencyConflictError):
            await h.container.initiate_screening_batch.execute(
                InitiateScreeningBatchRequestDTO(
                    tenant_id=tid,
                    requisition_id=req_id,
                    batch_label="cohort-modified",
                    files=(file1,),
                    idempotency_key="idem-001",
                    correlation_id="corr-002",
                )
            )

        # Exceeding tenant max_files_per_batch raises QuotaExceededError
        with pytest.raises(QuotaExceededError):
            await h.container.initiate_screening_batch.execute(
                InitiateScreeningBatchRequestDTO(
                    tenant_id=tid,
                    requisition_id=req_id,
                    batch_label="too-large",
                    files=(
                        file1,
                        BatchFileUploadSpecDTO(
                            "b.pdf", SHA256Hash.from_bytes(b"b").value, 2048, "application/pdf"
                        ),
                        BatchFileUploadSpecDTO(
                            "c.pdf", SHA256Hash.from_bytes(b"c").value, 2048, "application/pdf"
                        ),
                    ),
                    idempotency_key="idem-large",
                    correlation_id="corr-003",
                )
            )

        # Process the CV ingestion task (6 stages)
        instr = res.upload_instructions[0]
        await h.object_storage.put_bytes(
            tenant_id=tid,
            object_key=instr.object_key,
            payload=b"%PDF-1.7 clean candidate resume",
            content_type="application/pdf",
        )
        task_res = await h.container.process_document_task.execute(
            ProcessDocumentIngestionTaskRequestDTO(
                tenant_id=tid,
                batch_id=res.batch_id,
                task_id=instr.task_id,
                object_key=instr.object_key,
                correlation_id="corr-001",
            )
        )
        assert task_res.task_status is TaskIngestionStatus.COMPLETED
        assert task_res.batch_status is BatchStatus.COMPLETED
        assert task_res.candidate_profile_id is not None
        assert (tid.value, task_res.candidate_profile_id) in h.vector_search.indexed_profiles
        assert h.progress_publisher.events[-1].event_type == "TASK_COMPLETED"

        status_dto = await h.container.get_screening_batch_status.execute(
            GetScreeningBatchStatusQueryDTO(tenant_id=tid, batch_id=res.batch_id)
        )
        assert status_dto.completed_count == 1
        assert status_dto.pending_count == 0

        # Execute Hybrid Matching -> FAST_TRACK
        match_res = await h.container.execute_hybrid_matching.execute(
            ExecuteHybridMatchingRequestDTO(
                tenant_id=tid,
                requisition_id=req_id,
                candidate_profile_id=task_res.candidate_profile_id,
            )
        )
        assert match_res.gatekeeper_passed
        assert match_res.tier is CandidateTier.FAST_TRACK
        assert match_res.composite_score >= 88.0

    run_async(_run())


@pytest.mark.unit
def test_virus_quarantine_gatekeeper_rejection_and_suspended_tenant() -> None:
    async def _run() -> None:
        h = build_test_harness()
        tid, req_id = await _setup_published_req(h)
        assert isinstance(req_id, type(generate_uuid7()))

        # Virus-infected CV triggers QUARANTINED task and FAILED batch
        h.virus_scanner.infected_filenames.add("malware.pdf")
        batch_res = await h.container.initiate_screening_batch.execute(
            InitiateScreeningBatchRequestDTO(
                tenant_id=tid,
                requisition_id=req_id,
                batch_label="infected-cohort",
                files=(
                    BatchFileUploadSpecDTO(
                        filename="malware.pdf",
                        sha256_hex=SHA256Hash.from_bytes(b"EICAR").value,
                        file_size_bytes=2048,
                        mime_type="application/pdf",
                    ),
                ),
                idempotency_key="idem-malware",
                correlation_id="corr-mal",
            )
        )
        instr = batch_res.upload_instructions[0]
        await h.object_storage.put_bytes(
            tenant_id=tid,
            object_key=instr.object_key,
            payload=b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE",
            content_type="application/pdf",
        )
        quarantine_res = await h.container.process_document_task.execute(
            ProcessDocumentIngestionTaskRequestDTO(
                tenant_id=tid,
                batch_id=batch_res.batch_id,
                task_id=instr.task_id,
                object_key=instr.object_key,
                correlation_id="corr-mal",
            )
        )
        assert quarantine_res.task_status is TaskIngestionStatus.QUARANTINED
        assert quarantine_res.batch_status is BatchStatus.FAILED
        assert quarantine_res.error_code == "MALWARE_DETECTED"
        assert h.progress_publisher.events[-1].event_type == "TASK_QUARANTINED"

        # Junior CV failing 36-month Python gatekeeper -> score 0.0 & GATEKEEPER_REJECTED
        h.document_parser.applied_python_months = 6
        clean_batch = await h.container.initiate_screening_batch.execute(
            InitiateScreeningBatchRequestDTO(
                tenant_id=tid,
                requisition_id=req_id,
                batch_label="junior-cohort",
                files=(
                    BatchFileUploadSpecDTO(
                        filename="junior.pdf",
                        sha256_hex=SHA256Hash.from_bytes(b"junior").value,
                        file_size_bytes=2048,
                        mime_type="application/pdf",
                    ),
                ),
                idempotency_key="idem-jr",
                correlation_id="corr-jr",
            )
        )
        jr_instr = clean_batch.upload_instructions[0]
        jr_task = await h.container.process_document_task.execute(
            ProcessDocumentIngestionTaskRequestDTO(
                tenant_id=tid,
                batch_id=clean_batch.batch_id,
                task_id=jr_instr.task_id,
                object_key=jr_instr.object_key,
                correlation_id="corr-jr",
            )
        )
        assert jr_task.candidate_profile_id is not None
        jr_match = await h.container.execute_hybrid_matching.execute(
            ExecuteHybridMatchingRequestDTO(
                tenant_id=tid,
                requisition_id=req_id,
                candidate_profile_id=jr_task.candidate_profile_id,
            )
        )
        assert jr_match.tier is CandidateTier.GATEKEEPER_REJECTED
        assert jr_match.composite_score == 0.0
        assert not jr_match.gatekeeper_passed

        # Suspended tenant cannot initiate batch
        tenant_acc = h.container.repositories.tenant_repo.get_by_id(tid)
        assert tenant_acc is not None
        tenant_acc.suspend()
        h.container.repositories.tenant_repo.save(tenant_acc)
        with pytest.raises(UnauthorizedTenantAccessError):
            await h.container.initiate_screening_batch.execute(
                InitiateScreeningBatchRequestDTO(
                    tenant_id=tid,
                    requisition_id=req_id,
                    batch_label="blocked",
                    files=(
                        BatchFileUploadSpecDTO(
                            "x.pdf", SHA256Hash.from_bytes(b"x").value, 1024, "application/pdf"
                        ),
                    ),
                    idempotency_key="idem-susp",
                    correlation_id="corr-susp",
                )
            )

        # Draft requisition rejected
        draft = await h.container.create_job_requisition.execute(
            CreateJobRequisitionRequestDTO(
                tenant_id=tid,
                title="Draft Engineer Role",
                description="Draft description for testing unpublished rejection.",
                seniority_level=SeniorityLevel.MID,
                criteria=(
                    CompetencyCriterionInputDTO(
                        name="Python",
                        canonical_skill_id="SKILL_LANG_PYTHON",
                        weight=1.0,
                        target_depth=3,
                        is_gatekeeper=True,
                    ),
                ),
            )
        )
        with pytest.raises(ResourceConflictError):
            await h.container.initiate_screening_batch.execute(
                InitiateScreeningBatchRequestDTO(
                    tenant_id=tid,
                    requisition_id=draft.requisition_id,
                    batch_label="draft-batch",
                    files=(
                        BatchFileUploadSpecDTO(
                            "d.pdf", SHA256Hash.from_bytes(b"d").value, 1024, "application/pdf"
                        ),
                    ),
                    idempotency_key="idem-draft",
                    correlation_id="corr-draft",
                )
            )

    run_async(_run())
