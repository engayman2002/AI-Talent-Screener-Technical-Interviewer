"""ADR-006 Distributed Batch Ingestion Worker Coordinator with WFQ & Backpressure."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from uuid import UUID

from talent_screener.application.common.dtos import (
    RealtimeProgressEventDTO,
    TaskDispatchDTO,
)
from talent_screener.application.common.ports.outbound import RealtimeProgressPublisherPort
from talent_screener.application.document_intelligence.dtos import (
    GetScreeningBatchStatusQueryDTO,
    ProcessDocumentIngestionTaskRequestDTO,
    ProcessDocumentIngestionTaskResponseDTO,
)
from talent_screener.application.document_intelligence.ports.inbound import (
    GetScreeningBatchStatusPort,
    ProcessDocumentIngestionTaskPort,
)
from talent_screener.domain.contexts.document_intelligence.value_objects import (
    BatchStatus,
    TaskIngestionStatus,
)
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.messaging.in_memory_adapters import (
    InMemoryTaskQueueDispatcher,
)
from talent_screener.infrastructure.messaging.priority_rate_limiter import (
    PriorityReservedTokenBucketLimiter,
    RequestPriorityLane,
    compute_littles_law_concurrency,
    compute_queue_backpressure_delay,
)
from talent_screener.infrastructure.messaging.resilience import ResilientTaskExecutor

_LANE_BY_QUEUE: dict[str, RequestPriorityLane] = {
    "critical_interview": RequestPriorityLane.CRITICAL_INTERVIEW,
    "interactive_matching": RequestPriorityLane.INTERACTIVE_MATCHING,
    "bulk_ingestion": RequestPriorityLane.BULK_INGESTION,
    "pool_parse_pii": RequestPriorityLane.BULK_INGESTION,
    "pool_llm_extract": RequestPriorityLane.BULK_INGESTION,
}


@dataclass(frozen=True, slots=True)
class BatchWorkerMetrics:
    """Cumulative operational telemetry tracked across coordinator worker cycles."""

    total_dispatched: int
    total_succeeded: int
    total_failed: int
    total_deferred_backpressure: int
    concurrency_limit: int


@dataclass(frozen=True, slots=True)
class BatchExecutionSummary:
    """Summary emitted after draining and executing queued ingestion tasks."""

    processed_count: int
    succeeded_count: int
    failed_count: int
    deferred_count: int
    backpressure_delay_seconds: float
    completed_batch_ids: tuple[UUID, ...]


class BatchIngestionWorkerCoordinator:
    """Consumes queued ingestion tasks via WFQ, Little's Law bounds, and DLQ resilience."""

    def __init__(
        self,
        *,
        task_dispatcher: InMemoryTaskQueueDispatcher,
        process_task_use_case: ProcessDocumentIngestionTaskPort,
        get_batch_status_use_case: GetScreeningBatchStatusPort,
        progress_publisher: RealtimeProgressPublisherPort,
        rate_limiter: PriorityReservedTokenBucketLimiter | None = None,
        resilient_executor: ResilientTaskExecutor | None = None,
        estimated_tokens_per_doc: int = 4_200,
        max_batch_files: int = 500,
        worker_concurrency: int | None = None,
    ) -> None:
        self._dispatcher = task_dispatcher
        self._process_task = process_task_use_case
        self._get_batch_status = get_batch_status_use_case
        self._progress_publisher = progress_publisher
        self._rate_limiter = (
            rate_limiter if rate_limiter is not None else PriorityReservedTokenBucketLimiter()
        )
        self._resilient_executor = (
            resilient_executor if resilient_executor is not None else ResilientTaskExecutor()
        )
        self._tokens_per_doc = max(1, estimated_tokens_per_doc)
        self._max_batch_files = max(1, max_batch_files)
        self._concurrency = (
            worker_concurrency
            if worker_concurrency is not None
            else compute_littles_law_concurrency(
                target_throughput_per_sec=3.03,
                p95_latency_seconds=1.8,
                safety_factor=0.25,
            )
        )
        self._total_dispatched = 0
        self._total_succeeded = 0
        self._total_failed = 0
        self._total_deferred = 0
        self._announced_completed_batches: set[tuple[UUID, UUID]] = set()

    @property
    def concurrency_limit(self) -> int:
        """Configured Little's Law worker concurrency bound."""
        return self._concurrency

    def metrics(self) -> BatchWorkerMetrics:
        """Return cumulative coordinator execution counts."""
        return BatchWorkerMetrics(
            total_dispatched=self._total_dispatched,
            total_succeeded=self._total_succeeded,
            total_failed=self._total_failed,
            total_deferred_backpressure=self._total_deferred,
            concurrency_limit=self._concurrency,
        )

    def _schedule_weighted_fair_order(
        self, tasks: tuple[TaskDispatchDTO, ...]
    ) -> list[TaskDispatchDTO]:
        """Order tasks by priority lane, then Deficit-Round-Robin across active tenants."""
        by_lane: dict[RequestPriorityLane, dict[UUID, deque[TaskDispatchDTO]]] = {
            RequestPriorityLane.CRITICAL_INTERVIEW: defaultdict(deque),
            RequestPriorityLane.INTERACTIVE_MATCHING: defaultdict(deque),
            RequestPriorityLane.BULK_INGESTION: defaultdict(deque),
        }
        for item in tasks:
            lane = _LANE_BY_QUEUE.get(item.queue_name, RequestPriorityLane.BULK_INGESTION)
            by_lane[lane][item.tenant_id.value].append(item)

        ordered: list[TaskDispatchDTO] = []
        for lane in (
            RequestPriorityLane.CRITICAL_INTERVIEW,
            RequestPriorityLane.INTERACTIVE_MATCHING,
            RequestPriorityLane.BULK_INGESTION,
        ):
            tenant_queues = by_lane[lane]
            active_keys = deque(tenant_queues.keys())
            while active_keys:
                tenant_key = active_keys.popleft()
                q = tenant_queues[tenant_key]
                if q:
                    ordered.append(q.popleft())
                if q:
                    active_keys.append(tenant_key)
        return ordered

    async def process_pending_tasks(self, *, max_tasks: int | None = None) -> BatchExecutionSummary:
        """Drain pending queue tasks up to ``max_tasks``, enforce rate caps, and run workers."""
        cap = self._max_batch_files if max_tasks is None else min(max_tasks, self._max_batch_files)
        raw_tasks = self._dispatcher.drain_pending(cap)
        if not raw_tasks:
            return BatchExecutionSummary(
                processed_count=0,
                succeeded_count=0,
                failed_count=0,
                deferred_count=0,
                backpressure_delay_seconds=0.0,
                completed_batch_ids=(),
            )

        backpressure_delay = compute_queue_backpressure_delay(queue_depth=len(raw_tasks))
        scheduled = self._schedule_weighted_fair_order(raw_tasks)
        semaphore = asyncio.Semaphore(self._concurrency)

        runnable: list[TaskDispatchDTO] = []
        deferred: list[TaskDispatchDTO] = []
        for task in scheduled:
            lane = _LANE_BY_QUEUE.get(task.queue_name, RequestPriorityLane.BULK_INGESTION)
            acq = self._rate_limiter.try_acquire(lane=lane, tokens=self._tokens_per_doc)
            if not acq.granted:
                deferred.append(task)
                continue
            runnable.append(task)

        for item in deferred:
            self._dispatcher.requeue(item)
        self._total_deferred += len(deferred)

        results = await asyncio.gather(
            *(self._execute_one(task, semaphore) for task in runnable),
        )

        succeeded = sum(1 for ok, _, _ in results if ok)
        failed = len(results) - succeeded
        self._total_dispatched += len(results)
        self._total_succeeded += succeeded
        self._total_failed += failed

        completed_batches: list[UUID] = []
        checked_pairs = {(t_id, b_id) for _, t_id, b_id in results if b_id is not None}
        for tenant_id, batch_id in sorted(checked_pairs, key=lambda pair: str(pair[1])):
            if (tenant_id.value, batch_id) in self._announced_completed_batches:
                continue
            status_dto = await self._get_batch_status.execute(
                GetScreeningBatchStatusQueryDTO(tenant_id=tenant_id, batch_id=batch_id)
            )
            if status_dto.status in (
                BatchStatus.COMPLETED,
                BatchStatus.PARTIALLY_FAILED,
                BatchStatus.FAILED,
            ):
                self._announced_completed_batches.add((tenant_id.value, batch_id))
                completed_batches.append(batch_id)
                await self._progress_publisher.publish_progress(
                    RealtimeProgressEventDTO(
                        tenant_id=tenant_id,
                        channel=f"batch:{batch_id}",
                        event_type="BATCH_COMPLETED",
                        payload={
                            "batch_id": str(batch_id),
                            "status": status_dto.status.value,
                            "total_files": status_dto.total_files,
                            "completed_count": status_dto.completed_count,
                            "failed_count": status_dto.failed_count,
                        },
                    )
                )

        return BatchExecutionSummary(
            processed_count=len(results),
            succeeded_count=succeeded,
            failed_count=failed,
            deferred_count=len(deferred),
            backpressure_delay_seconds=backpressure_delay,
            completed_batch_ids=tuple(completed_batches),
        )

    async def _execute_one(
        self, task: TaskDispatchDTO, semaphore: asyncio.Semaphore
    ) -> tuple[bool, TenantId, UUID | None]:
        batch_id_raw = str(task.payload.get("batch_id", ""))
        task_id_raw = str(task.payload.get("task_id", ""))
        object_key = str(task.payload.get("object_key", ""))
        if not batch_id_raw or not task_id_raw or not object_key:
            return False, task.tenant_id, None

        batch_id = UUID(batch_id_raw)
        task_id = UUID(task_id_raw)
        channel = f"batch:{batch_id}"

        async with semaphore:
            await self._progress_publisher.publish_progress(
                RealtimeProgressEventDTO(
                    tenant_id=task.tenant_id,
                    channel=channel,
                    event_type="TASK_STARTED",
                    payload={
                        "batch_id": str(batch_id),
                        "task_id": str(task_id),
                        "queue_name": task.queue_name,
                    },
                )
            )

            async def _run_task() -> ProcessDocumentIngestionTaskResponseDTO:
                return await self._process_task.execute(
                    ProcessDocumentIngestionTaskRequestDTO(
                        tenant_id=task.tenant_id,
                        batch_id=batch_id,
                        task_id=task_id,
                        object_key=object_key,
                        correlation_id=task.correlation_id,
                    )
                )

            outcome = await self._resilient_executor.run_with_dlq(
                tenant_id=task.tenant_id,
                task_name=task.task_name,
                correlation_id=task.correlation_id,
                payload=task.payload,
                operation=_run_task,
            )

            if outcome.succeeded and outcome.result is not None:
                res = outcome.result
                is_clean_ok = res.task_status is TaskIngestionStatus.COMPLETED
                await self._progress_publisher.publish_progress(
                    RealtimeProgressEventDTO(
                        tenant_id=task.tenant_id,
                        channel=channel,
                        event_type="TASK_SUCCEEDED" if is_clean_ok else "TASK_FAILED",
                        payload={
                            "batch_id": str(batch_id),
                            "task_id": str(task_id),
                            "task_status": res.task_status.value,
                            "batch_status": res.batch_status.value,
                            "error_code": res.error_code or "",
                        },
                    )
                )
                return is_clean_ok, task.tenant_id, batch_id

            await self._progress_publisher.publish_progress(
                RealtimeProgressEventDTO(
                    tenant_id=task.tenant_id,
                    channel=channel,
                    event_type="TASK_FAILED",
                    payload={
                        "batch_id": str(batch_id),
                        "task_id": str(task_id),
                        "error_code": (
                            outcome.dead_letter_record.error_code
                            if outcome.dead_letter_record is not None
                            else "WORKER_EXECUTION_FAILED"
                        ),
                    },
                )
            )
            return False, task.tenant_id, batch_id
