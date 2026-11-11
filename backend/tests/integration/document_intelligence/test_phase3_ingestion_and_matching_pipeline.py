"""Integration tests for Phase 3 Resilience, DLQ & End-to-End Document/Matching Pipeline."""

from __future__ import annotations

import asyncio
import io
import zipfile
from collections.abc import Coroutine
from typing import Any
from xml.sax.saxutils import escape

import pytest

from talent_screener.application.common.dtos import FailureClassification
from talent_screener.application.common.exceptions import (
    ExternalAdapterFailureError,
    GuardrailViolationError,
)
from talent_screener.application.document_intelligence.dtos import (
    BatchFileUploadSpecDTO,
    ExecuteHybridMatchingRequestDTO,
    GetScreeningBatchStatusQueryDTO,
    InitiateScreeningBatchRequestDTO,
    PresignedUploadInstructionDTO,
    ProcessDocumentIngestionTaskRequestDTO,
    ProcessDocumentIngestionTaskResponseDTO,
)
from talent_screener.application.identity_governance.dtos import (
    ExecuteCandidateErasureRequestDTO,
    ProvisionTenantAccountRequestDTO,
    RecordCandidateConsentRequestDTO,
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
from talent_screener.composition_root import build_phase3_container
from talent_screener.domain.contexts.document_intelligence import (
    BatchStatus,
    CandidateTier,
    TaskIngestionStatus,
)
from talent_screener.domain.contexts.document_intelligence.matching.events import (
    CandidateMatchedToJobEvent,
    CandidateRejectedByGatekeeperEvent,
    CandidateShortlistedForInterviewEvent,
)
from talent_screener.domain.contexts.identity_governance import ConsentScope
from talent_screener.domain.contexts.recruitment import SeniorityLevel
from talent_screener.domain.shared import SHA256Hash, TenantId
from talent_screener.infrastructure.messaging import (
    CircuitBreaker,
    ExponentialBackoffRetryPolicy,
    InMemoryDeadLetterQueue,
    ResilientTaskExecutor,
    classify_failure,
)
from talent_screener.infrastructure.persistence import InMemoryHybridVectorStoreBackend


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _make_docx_cv(paragraphs: tuple[str, ...]) -> bytes:
    buf = io.BytesIO()
    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", b'<?xml version="1.0"?><Types/>')
        zf.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


@pytest.mark.unit
def test_retry_backoff_full_jitter_circuit_breaker_and_dlq_routing() -> None:
    async def _run_test() -> None:
        policy = ExponentialBackoffRetryPolicy(
            base_delay_seconds=1.5,
            max_delay_seconds=30.0,
            max_retries=4,
        )
        # Verify upper caps min(30.0, 1.5 * 2^k): 1.5, 3.0, 6.0, 12.0, 24.0, 30.0
        assert policy.upper_bound_seconds(0) == 1.5
        assert policy.upper_bound_seconds(1) == 3.0
        assert policy.upper_bound_seconds(2) == 6.0
        assert policy.upper_bound_seconds(3) == 12.0
        assert policy.upper_bound_seconds(5) == 30.0

        for k in range(6):
            sample = policy.compute_sleep_seconds(k, uniform_fn=lambda lo, hi: (lo + hi) * 0.5)
            assert 0.0 <= sample <= min(30.0, 1.5 * (2.0**k))

        async def _no_sleep(_: float) -> None:
            return None

        dlq = InMemoryDeadLetterQueue()
        executor = ResilientTaskExecutor(
            retry_policy=policy,
            dlq=dlq,
            sleep_fn=_no_sleep,
            uniform_fn=lambda lo, hi: hi * 0.5,
        )
        tid = TenantId.generate()

        # 1. Transient outage succeeds after 2 retries without reaching DLQ
        attempts_1 = 0

        async def _flaky_op() -> str:
            nonlocal attempts_1
            attempts_1 += 1
            if attempts_1 < 3:
                raise ExternalAdapterFailureError("429 Rate Limit", code="HTTP_429", retryable=True)
            return "OK"

        ok_outcome = await executor.run_with_dlq(
            tenant_id=tid,
            task_name="ingest_cv",
            correlation_id="corr-flaky",
            payload={"file": "alice.pdf"},
            operation=_flaky_op,
        )
        assert ok_outcome.succeeded and ok_outcome.result == "OK"
        assert ok_outcome.attempt_count == 3
        assert ok_outcome.sleep_delays_seconds == (0.75, 1.5)
        assert await dlq.list_by_tenant(tid) == ()

        # 2. Permanent failure bypasses retries and goes straight to DLQ
        perm_attempts = 0

        async def _permanent_op() -> str:
            nonlocal perm_attempts
            perm_attempts += 1
            raise GuardrailViolationError("Prompt injection detected")

        perm_outcome = await executor.run_with_dlq(
            tenant_id=tid,
            task_name="ingest_cv",
            correlation_id="corr-perm",
            payload={"file": "evil.pdf"},
            operation=_permanent_op,
        )
        assert not perm_outcome.succeeded
        assert perm_outcome.attempt_count == 1
        assert perm_outcome.sleep_delays_seconds == ()
        assert perm_outcome.classification is FailureClassification.PERMANENT
        assert perm_outcome.dead_letter_record is not None

        # 3. Exhausted transient retries route to DLQ after max_retries + 1 attempts
        async def _always_timeout() -> str:
            raise TimeoutError("upstream timeout")

        exhausted = await executor.run_with_dlq(
            tenant_id=tid,
            task_name="ingest_cv",
            correlation_id="corr-timeout",
            payload={"file": "slow.pdf"},
            operation=_always_timeout,
        )
        assert not exhausted.succeeded
        assert exhausted.attempt_count == 5  # initial try + 4 retries
        assert len(exhausted.sleep_delays_seconds) == 4
        assert exhausted.classification is FailureClassification.TRANSIENT
        assert len(await dlq.list_by_tenant(tid)) == 2
        assert classify_failure(ValueError("bad schema")) is FailureClassification.PERMANENT

        # 4. Circuit breaker transitions CLOSED -> OPEN -> HALF_OPEN -> CLOSED
        now = 100.0
        breaker = CircuitBreaker(
            failure_threshold=2,
            recovery_timeout_seconds=10.0,
            clock_fn=lambda: now,
        )
        assert str(breaker.state) == "CLOSED"
        breaker.record_failure()
        breaker.record_failure()
        assert str(breaker.state) == "OPEN"
        with pytest.raises(ExternalAdapterFailureError):
            breaker.ensure_closed()
        now += 12.0
        breaker.ensure_closed()
        assert str(breaker.state) == "HALF_OPEN"
        breaker.record_success()
        assert str(breaker.state) == "CLOSED"

    _run(_run_test())


@pytest.mark.integration
def test_end_to_end_phase3_ingestion_matching_dlq_and_gdpr_pipeline() -> None:
    async def _run_test() -> None:
        vector_backend = InMemoryHybridVectorStoreBackend()
        bundle = build_phase3_container(vector_backend=vector_backend)
        c = bundle.container

        # 1. Provision Tenant & Create/Publish JobRequisition
        tenant = await c.provision_tenant_account.execute(
            ProvisionTenantAccountRequestDTO(
                organization_slug="acme-ai",
                display_name="Acme AI Corp",
                max_files_per_batch=50,
            )
        )
        tid = tenant.tenant_id

        req = await c.create_job_requisition.execute(
            CreateJobRequisitionRequestDTO(
                tenant_id=tid,
                title="Senior Python & Distributed Systems Engineer",
                description="Architect Python asyncio microservices and distributed systems.",
                seniority_level=SeniorityLevel.SENIOR,
                criteria=(
                    CompetencyCriterionInputDTO(
                        name="Python Asyncio Microservices",
                        canonical_skill_id="SKILL_LANG_PYTHON",
                        weight=0.55,
                        target_depth=4,
                        is_gatekeeper=True,
                        min_applied_months=36,
                    ),
                    CompetencyCriterionInputDTO(
                        name="Distributed Systems Consensus",
                        canonical_skill_id="SKILL_DISTRIBUTED_SYSTEMS",
                        weight=0.45,
                        target_depth=4,
                        is_gatekeeper=False,
                    ),
                ),
            )
        )
        await c.publish_job_requisition.execute(
            PublishJobRequisitionRequestDTO(tenant_id=tid, requisition_id=req.requisition_id)
        )

        # Prepare 4 realistic payloads:
        # (A) Senior strong candidate PDF with real PII
        senior_pdf = (
            b"%PDF-1.7\n"
            b"Name: Senior Architect\n"
            b"Email: senior.arch@example.com\n"
            b"Phone: +1-555-314-1592\n"
            b"SUMMARY\n"
            b"Senior backend engineer building distributed cloud backends.\n"
            b"EXPERIENCE\n"
            b"Senior Distributed Systems Engineer 2020-01 to Present\n"
            b"Architected Python asyncio microservices handling 45k req/sec with Raft "
            b"consensus and zero data loss across multi-region clusters. Reduced P99 "
            b"latency by 52%.\n"
        )
        # (B) Stale / thin DOCX candidate missing deep Distributed Systems claim
        stale_docx = _make_docx_cv(
            (
                "Name: Historical Dev",
                "Email: stale.dev@example.com",
                "EXPERIENCE",
                "Backend Developer 2017-01 to 2020-06",
                "Built Python scripts and REST microservices for internal batch jobs.",
                "SKILLS",
                "Python, Distributed Systems",
            )
        )
        # (C) Junior PDF failing 36-month Python gatekeeper
        junior_pdf = (
            b"%PDF-1.7\n"
            b"Name: Junior Dev\n"
            b"Email: junior.dev@example.com\n"
            b"EXPERIENCE\n"
            b"Junior Engineer 2024-01 to 2024-06\n"
            b"Implemented small Python asyncio utilities.\n"
        )
        # (D) Corrupted empty/unparseable PDF
        corrupt_pdf = b"%PDF-1.7\n%%EOF"

        files_spec = (
            BatchFileUploadSpecDTO(
                filename="senior.pdf",
                sha256_hex=SHA256Hash.from_bytes(senior_pdf).value,
                file_size_bytes=len(senior_pdf),
                mime_type="application/pdf",
            ),
            BatchFileUploadSpecDTO(
                filename="stale.docx",
                sha256_hex=SHA256Hash.from_bytes(stale_docx).value,
                file_size_bytes=len(stale_docx),
                mime_type=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
            ),
            BatchFileUploadSpecDTO(
                filename="junior.pdf",
                sha256_hex=SHA256Hash.from_bytes(junior_pdf).value,
                file_size_bytes=len(junior_pdf),
                mime_type="application/pdf",
            ),
            BatchFileUploadSpecDTO(
                filename="corrupt.pdf",
                sha256_hex=SHA256Hash.from_bytes(corrupt_pdf).value,
                file_size_bytes=len(corrupt_pdf),
                mime_type="application/pdf",
            ),
        )

        batch_res = await c.initiate_screening_batch.execute(
            InitiateScreeningBatchRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                batch_label="phase3-e2e-cohort",
                files=files_spec,
                idempotency_key="phase3-cohort-key",
                correlation_id="corr-e2e",
            )
        )
        # Rubric should be automatically locked via ScreeningBatchCreatedEvent choreography
        req_after = c.repositories.requisition_repo.get_by_id(tid, req.requisition_id)
        assert req_after is not None and req_after.is_rubric_locked

        payloads_by_file = {
            "senior.pdf": (senior_pdf, "application/pdf"),
            "stale.docx": (
                stale_docx,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            "junior.pdf": (junior_pdf, "application/pdf"),
            "corrupt.pdf": (corrupt_pdf, "application/pdf"),
        }

        profile_ids: dict[str, Any] = {}

        async def _execute_instr(
            instruction: PresignedUploadInstructionDTO,
        ) -> ProcessDocumentIngestionTaskResponseDTO:
            return await c.process_document_task.execute(
                ProcessDocumentIngestionTaskRequestDTO(
                    tenant_id=tid,
                    batch_id=batch_res.batch_id,
                    task_id=instruction.task_id,
                    object_key=instruction.object_key,
                    correlation_id=f"corr-{instruction.filename}",
                )
            )

        for instr in batch_res.upload_instructions:
            raw_bytes, mime = payloads_by_file[instr.filename]
            await bundle.adapters.object_storage.put_bytes(
                tenant_id=tid,
                object_key=instr.object_key,
                payload=raw_bytes,
                content_type=mime,
            )

            async def _op(
                instruction: PresignedUploadInstructionDTO = instr,
            ) -> ProcessDocumentIngestionTaskResponseDTO:
                return await _execute_instr(instruction)

            outcome = await bundle.resilient_executor.run_with_dlq(
                tenant_id=tid,
                task_name="process_document_ingestion_task",
                correlation_id=f"corr-{instr.filename}",
                payload={"task_id": str(instr.task_id), "filename": instr.filename},
                operation=_op,
            )
            if instr.filename == "corrupt.pdf":
                assert not outcome.succeeded
                assert outcome.attempt_count == 1
                assert outcome.classification is FailureClassification.PERMANENT
            else:
                assert outcome.succeeded and outcome.result is not None
                assert outcome.result.task_status is TaskIngestionStatus.COMPLETED
                profile_ids[instr.filename] = outcome.result.candidate_profile_id

        # Batch reaches PARTIALLY_FAILED (3 succeeded, 1 permanently failed to DLQ)
        batch_status = await c.get_screening_batch_status.execute(
            GetScreeningBatchStatusQueryDTO(
                tenant_id=tid,
                batch_id=batch_res.batch_id,
            )
        )
        assert batch_status.status is BatchStatus.PARTIALLY_FAILED
        assert batch_status.completed_count == 3
        assert batch_status.failed_count == 1
        assert batch_status.pending_count == 0
        dlq_records = await bundle.dlq.list_by_tenant(tid)
        assert len(dlq_records) == 1
        assert dlq_records[0].error_code == "ZERO_TEXT_EXTRACTABLE"

        # Verify PII was scrubbed from CandidateProfile & indexed vectors for senior candidate
        senior_profile = c.repositories.profile_repo.get_by_id(tid, profile_ids["senior.pdf"])
        assert senior_profile is not None
        assert senior_profile.pii_redaction_map.redacted_entity_count >= 3
        senior_pts = await vector_backend.list_candidate_points(
            tenant_id=tid,
            requisition_id=req.requisition_id,
            candidate_profile_id=senior_profile.id,
        )
        assert senior_pts
        assert all("senior.arch@example.com" not in pt.contextualized_text for pt in senior_pts)

        # Execute Hybrid Matching across all 3 valid candidates:
        senior_match = await c.execute_hybrid_matching.execute(
            ExecuteHybridMatchingRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                candidate_profile_id=profile_ids["senior.pdf"],
            )
        )
        assert senior_match.gatekeeper_passed
        assert senior_match.tier in {CandidateTier.FAST_TRACK, CandidateTier.INTERVIEW}
        assert senior_match.composite_score >= 75.0

        stale_match = await c.execute_hybrid_matching.execute(
            ExecuteHybridMatchingRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                candidate_profile_id=profile_ids["stale.docx"],
            )
        )
        assert stale_match.composite_score < senior_match.composite_score
        # Verify EpistemicSkillGaps are emitted cleanly for Interview probing
        assert len(stale_match.skill_gaps) >= 1

        junior_match = await c.execute_hybrid_matching.execute(
            ExecuteHybridMatchingRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                candidate_profile_id=profile_ids["junior.pdf"],
            )
        )
        assert not junior_match.gatekeeper_passed
        assert junior_match.tier is CandidateTier.GATEKEEPER_REJECTED
        assert junior_match.composite_score == 0.0
        assert "SKILL_LANG_PYTHON" in junior_match.failed_gatekeepers

        # Verify domain events emitted during matching & ingestion
        uow_events = c.unit_of_work.dispatched_events  # type: ignore[attr-defined]
        assert any(isinstance(ev, CandidateMatchedToJobEvent) for ev in uow_events)
        assert any(isinstance(ev, CandidateShortlistedForInterviewEvent) for ev in uow_events)
        assert any(isinstance(ev, CandidateRejectedByGatekeeperEvent) for ev in uow_events)

        # Idempotent replay on InMemoryIdempotencyStore returns same batch without duplicating work.
        replay_batch = await c.initiate_screening_batch.execute(
            InitiateScreeningBatchRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                batch_label="phase3-e2e-cohort",
                files=files_spec,
                idempotency_key="phase3-cohort-key",
                correlation_id="corr-e2e-replay",
            )
        )
        assert replay_batch.replayed_from_idempotency
        assert replay_batch.batch_id == batch_res.batch_id

        # Record consent & smoke-test Live Interview provisioning with Phase 3 EpistemicSkillGaps
        await c.record_candidate_consent.execute(
            RecordCandidateConsentRequestDTO(
                tenant_id=tid,
                candidate_id=senior_profile.id,
                granted_scopes=(
                    ConsentScope.AI_SCREENING,
                    ConsentScope.VOICE_INTERVIEW_RECORDING,
                    ConsentScope.INTEGRITY_TELEMETRY,
                ),
            )
        )
        session = await c.initialize_interview_session.execute(
            InitializeInterviewSessionRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                candidate_profile_id=senior_profile.id,
            )
        )
        turn = await c.process_candidate_turn.execute(
            ProcessCandidateTurnRequestDTO(
                tenant_id=tid,
                session_id=session.session_id,
                turn_number=1,
                checkpoint_version=session.checkpoint_version,
                candidate_utterance="We designed fencing tokens and asynchronous outbox queues.",
                remaining_seconds=1500,
                telemetry=ClientTelemetryPayloadDTO(
                    focus_loss_count=0,
                    focus_loss_duration_ms=0,
                    external_paste_detected=False,
                    paste_char_count=0,
                    keystroke_anomaly_detected=False,
                    response_latency_ms=1100,
                    speech_cadence_wpm=130.0,
                    pause_entropy=0.65,
                ),
                correlation_id="corr-turn-1",
                synthesize_voice=True,
            )
        )
        assert turn.next_question

        # Exercise GDPR crypto-shred + vector erasure on senior candidate
        erasure = await c.execute_candidate_erasure.execute(
            ExecuteCandidateErasureRequestDTO(
                tenant_id=tid,
                candidate_id=senior_profile.id,
                reason="Candidate withdrew application",
            )
        )
        assert erasure.vectors_purged
        assert (
            await vector_backend.list_candidate_points(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                candidate_profile_id=senior_profile.id,
            )
            == ()
        )

        # Malware EICAR PDF inside a fresh batch is quarantined via VirusScannerPort.
        mal_bytes = b"%PDF-1.7\nEICAR-STANDARD-ANTIVIRUS-TEST-FILE"
        mal_batch = await c.initiate_screening_batch.execute(
            InitiateScreeningBatchRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                batch_label="malware-quarantine-cohort",
                files=(
                    BatchFileUploadSpecDTO(
                        filename="eicar.pdf",
                        sha256_hex=SHA256Hash.from_bytes(mal_bytes).value,
                        file_size_bytes=len(mal_bytes),
                        mime_type="application/pdf",
                    ),
                ),
                idempotency_key="phase3-malware-key",
                correlation_id="corr-mal",
            )
        )
        mal_instr = mal_batch.upload_instructions[0]
        await bundle.adapters.object_storage.put_bytes(
            tenant_id=tid,
            object_key=mal_instr.object_key,
            payload=mal_bytes,
            content_type="application/pdf",
        )
        mal_res = await c.process_document_task.execute(
            ProcessDocumentIngestionTaskRequestDTO(
                tenant_id=tid,
                batch_id=mal_batch.batch_id,
                task_id=mal_instr.task_id,
                object_key=mal_instr.object_key,
                correlation_id="corr-mal",
            )
        )
        assert mal_res.task_status is TaskIngestionStatus.QUARANTINED
        assert mal_res.error_code == "MALWARE_DETECTED"

    _run(_run_test())
