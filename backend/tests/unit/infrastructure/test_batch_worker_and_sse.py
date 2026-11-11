"""Unit tests for ADR-006 Priority Token Bucket, Batch Worker Coordinator & Replayable SSE."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from talent_screener.application.common.dtos import RealtimeProgressEventDTO, TaskDispatchDTO
from talent_screener.application.document_intelligence.dtos import (
    BatchFileUploadSpecDTO,
    InitiateScreeningBatchRequestDTO,
)
from talent_screener.application.identity_governance.dtos import ProvisionTenantAccountRequestDTO
from talent_screener.application.recruitment.dtos import (
    CompetencyCriterionInputDTO,
    CreateJobRequisitionRequestDTO,
    PublishJobRequisitionRequestDTO,
)
from talent_screener.composition_root import build_phase5_container
from talent_screener.domain.contexts.document_intelligence import BatchStatus
from talent_screener.domain.contexts.recruitment import SeniorityLevel
from talent_screener.domain.shared import SHA256Hash, TenantId, generate_uuid7
from talent_screener.infrastructure.messaging import (
    BatchIngestionWorkerCoordinator,
    InMemoryTaskQueueDispatcher,
    PriorityReservedTokenBucketLimiter,
    ReplayableSseProgressBroker,
    RequestPriorityLane,
    compute_littles_law_concurrency,
    compute_queue_backpressure_delay,
)
from talent_screener.presentation.rest import format_sse_frame


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def test_priority_reserved_token_bucket_protects_interview_capacity() -> None:
    clock = 100.0
    limiter = PriorityReservedTokenBucketLimiter(
        total_capacity=10_000,
        interview_reserve=2_500,
        matching_reserve=1_500,
        refill_tokens_per_second=1_000.0,
        clock_fn=lambda: clock,
    )

    # Bulk reserve floor = 4000 -> bulk usable headroom = 6000
    b1 = limiter.try_acquire(lane=RequestPriorityLane.BULK_INGESTION, tokens=4_000)
    assert b1.granted
    assert b1.available_after == 6_000

    # Asking for 2500 more drops projected below 4000 -> BULK rejected cleanly
    b2 = limiter.try_acquire(lane=RequestPriorityLane.BULK_INGESTION, tokens=2_500)
    assert not b2.granted
    assert b2.reason_code == "BULK_BACKPRESSURE_RESERVED_CAPACITY"
    assert b2.retry_after_seconds > 0.0

    # INTERACTIVE_MATCHING reserve floor = 2500 -> can still acquire 3000 (down to 3000)
    m1 = limiter.try_acquire(lane=RequestPriorityLane.INTERACTIVE_MATCHING, tokens=3_000)
    assert m1.granted
    assert m1.available_after == 3_000

    # Further matching drops below 2500 -> denied, BUT CRITICAL_INTERVIEW succeeds cleanly!
    m2 = limiter.try_acquire(lane=RequestPriorityLane.INTERACTIVE_MATCHING, tokens=1_000)
    assert not m2.granted
    i1 = limiter.try_acquire(lane=RequestPriorityLane.CRITICAL_INTERVIEW, tokens=2_500)
    assert i1.granted
    assert i1.available_after == 500

    # Advance clock 5 seconds -> +5000 tokens refilled (5500 total) -> bulk works for 1500
    clock += 5.0
    snap = limiter.snapshot()
    assert snap.available_tokens == 5_500
    assert snap.bulk_available == 1_500
    assert compute_littles_law_concurrency(
        target_throughput_per_sec=3.03, p95_latency_seconds=1.8, safety_factor=0.25
    ) == 7
    assert compute_queue_backpressure_delay(queue_depth=250) == 0.0
    assert compute_queue_backpressure_delay(queue_depth=600, max_pause_seconds=5.0) == 5.0


def test_batch_worker_coordinator_wfq_scheduling_and_sse_replay() -> None:
    async def _run_test() -> None:
        bundle = build_phase5_container(
            bucket_total_capacity=50_000,
            bucket_interview_reserve=10_000,
            bucket_matching_reserve=5_000,
            bucket_refill_tokens_per_second=0.0,
        )
        c = bundle.container
        t_prov = await c.provision_tenant_account.execute(
            ProvisionTenantAccountRequestDTO(
                organization_slug="worker-org",
                display_name="Worker Test Org",
            )
        )
        tid = t_prov.tenant_id

        req = await c.create_job_requisition.execute(
            CreateJobRequisitionRequestDTO(
                tenant_id=tid,
                title="Senior Python Platform Engineer",
                description="Build distributed asyncio workers.",
                seniority_level=SeniorityLevel.SENIOR,
                criteria=(
                    CompetencyCriterionInputDTO(
                        name="Python Async",
                        canonical_skill_id="SKILL_LANG_PYTHON",
                        weight=1.0,
                        target_depth=4,
                        is_gatekeeper=True,
                        min_applied_months=24,
                    ),
                ),
            )
        )
        await c.publish_job_requisition.execute(
            PublishJobRequisitionRequestDTO(tenant_id=tid, requisition_id=req.requisition_id)
        )

        clean_pdf = (
            b"%PDF-1.7\n"
            b"Name: Senior Dev\n"
            b"EXPERIENCE\n"
            b"Senior Engineer 2020-01 to Present\n"
            b"Built Python asyncio pipelines processing 30k QPS cleanly.\n"
        )
        corrupt_pdf = b"%PDF-1.7\n%%EOF"

        batch = await c.initiate_screening_batch.execute(
            InitiateScreeningBatchRequestDTO(
                tenant_id=tid,
                requisition_id=req.requisition_id,
                batch_label="worker-batch-1",
                files=(
                    BatchFileUploadSpecDTO(
                        filename="good.pdf",
                        sha256_hex=SHA256Hash.from_bytes(clean_pdf).value,
                        file_size_bytes=len(clean_pdf),
                        mime_type="application/pdf",
                    ),
                    BatchFileUploadSpecDTO(
                        filename="bad.pdf",
                        sha256_hex=SHA256Hash.from_bytes(corrupt_pdf).value,
                        file_size_bytes=len(corrupt_pdf),
                        mime_type="application/pdf",
                    ),
                ),
                idempotency_key="worker-idem-1",
                correlation_id="corr-worker-1",
            )
        )
        for instr in batch.upload_instructions:
            raw = clean_pdf if instr.filename == "good.pdf" else corrupt_pdf
            await bundle.adapters.object_storage.put_bytes(
                tenant_id=tid,
                object_key=instr.object_key,
                payload=raw,
                content_type="application/pdf",
            )

        summary = await bundle.batch_coordinator.process_pending_tasks()
        assert summary.processed_count == 2
        assert summary.succeeded_count == 1
        assert summary.failed_count == 1
        assert batch.batch_id in summary.completed_batch_ids

        # Replay all SSE events and then replay strictly AFTER event_id=2 (Last-Event-ID resumption)
        channel = f"batch:{batch.batch_id}"
        all_events = bundle.sse_broker.replay_since(tenant_id=tid, channel=channel)
        assert len(all_events) >= 4
        event_types = [ev.event_type for ev in all_events]
        assert "TASK_STARTED" in event_types
        assert "TASK_SUCCEEDED" in event_types
        assert "TASK_FAILED" in event_types
        assert event_types[-1] == "BATCH_COMPLETED"

        partial = bundle.sse_broker.replay_since(tenant_id=tid, channel=channel, last_event_id=2)
        assert all(ev.event_id > 2 for ev in partial)
        assert len(partial) == len(all_events) - 2
        sse_wire = format_sse_frame(partial[0])
        assert f"id: {partial[0].event_id}\n" in sse_wire
        assert f"event: {partial[0].event_type}\n" in sse_wire

        # Test WFQ interleaving order across 2 tenants + priority lane order
        t_other = TenantId.generate()
        coord = BatchIngestionWorkerCoordinator(
            task_dispatcher=InMemoryTaskQueueDispatcher(),
            process_task_use_case=c.process_document_task,
            get_batch_status_use_case=c.get_screening_batch_status,
            progress_publisher=bundle.sse_broker,
        )
        sample_tasks = (
            TaskDispatchDTO(t_prov.tenant_id, "bulk_ingestion", "op", "1", {"idx": 1}),
            TaskDispatchDTO(t_prov.tenant_id, "bulk_ingestion", "op", "2", {"idx": 2}),
            TaskDispatchDTO(t_other, "bulk_ingestion", "op", "3", {"idx": 3}),
            TaskDispatchDTO(t_other, "critical_interview", "op", "4", {"idx": 4}),
        )
        scheduled = coord._schedule_weighted_fair_order(sample_tasks)
        assert scheduled[0].queue_name == "critical_interview"
        # Bulk lane interleaves t_prov and t_other before 2nd t_prov task
        assert scheduled[1].tenant_id == t_prov.tenant_id
        assert scheduled[2].tenant_id == t_other
        assert scheduled[3].tenant_id == t_prov.tenant_id
        assert bundle.batch_coordinator.metrics().total_succeeded == 1
        assert BatchStatus.PARTIALLY_FAILED.value == "PARTIALLY_FAILED"

    _run(_run_test())


def test_sse_broker_ring_buffer_subscription_and_tenant_isolation() -> None:
    async def _run_test() -> None:
        broker = ReplayableSseProgressBroker(ring_buffer_size=3)
        t1 = TenantId.generate()
        t2 = TenantId.generate()
        sub = broker.subscribe(tenant_id=t1, channel="batch:abc")

        for idx in range(5):
            await broker.publish_progress(
                RealtimeProgressEventDTO(
                    tenant_id=t1,
                    channel="batch:abc",
                    event_type=f"EV_{idx}",
                    payload={"seq": idx},
                )
            )
        replayed = broker.replay_since(tenant_id=t1, channel="batch:abc")
        assert [ev.event_id for ev in replayed] == [3, 4, 5]
        assert broker.latest_event_id(tenant_id=t1, channel="batch:abc") == 5
        assert broker.replay_since(tenant_id=t2, channel="batch:abc") == ()

        first_live = await sub.queue.get()
        assert first_live.event_id == 1
        broker.unsubscribe(sub)
        _ = generate_uuid7()

    _run(_run_test())
