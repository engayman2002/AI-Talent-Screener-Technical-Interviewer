"""Cross-context Domain Event Handler locking JobRequisition rubrics idempotently."""

from __future__ import annotations

from talent_screener.application.recruitment.dtos import LockJobRequisitionRubricRequestDTO
from talent_screener.application.recruitment.ports.inbound import LockJobRequisitionRubricPort
from talent_screener.domain.contexts.document_intelligence.events import (
    ScreeningBatchCreatedEvent,
)
from talent_screener.domain.contexts.live_interview.events import InterviewSessionStartedEvent


class LockRubricOnBatchOrInterviewStartedHandler:
    """Locks associated JobRequisition rubric when a batch or interview starts."""

    def __init__(self, *, lock_use_case: LockJobRequisitionRubricPort) -> None:
        self._lock_use_case = lock_use_case

    async def handle(
        self,
        event: ScreeningBatchCreatedEvent | InterviewSessionStartedEvent,
    ) -> None:
        if isinstance(event, ScreeningBatchCreatedEvent):
            request = LockJobRequisitionRubricRequestDTO(
                tenant_id=event.tenant_id,
                requisition_id=event.requisition_id,
                lock_source_type="SCREENING_BATCH",
                lock_source_id=event.batch_id,
            )
        else:
            request = LockJobRequisitionRubricRequestDTO(
                tenant_id=event.tenant_id,
                requisition_id=event.requisition_id,
                lock_source_type="INTERVIEW_SESSION",
                lock_source_id=event.session_id,
            )
        await self._lock_use_case.execute(request)
