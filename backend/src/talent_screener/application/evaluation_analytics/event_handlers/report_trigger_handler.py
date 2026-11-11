"""Cross-context Domain Event Handler synthesizing report upon interview completion."""

from __future__ import annotations

from talent_screener.application.evaluation_analytics.dtos import (
    GenerateEvaluationReportRequestDTO,
)
from talent_screener.application.evaluation_analytics.ports.inbound import (
    GenerateEvaluationReportPort,
)
from talent_screener.domain.contexts.live_interview.events import (
    InterviewSessionCompletedEvent,
)


class TriggerEvaluationReportOnInterviewCompletedHandler:
    """Automatically triggers EvaluationReport synthesis on InterviewSessionCompletedEvent."""

    def __init__(self, *, generate_report_use_case: GenerateEvaluationReportPort) -> None:
        self._generate_report_use_case = generate_report_use_case

    async def handle(self, event: InterviewSessionCompletedEvent) -> None:
        await self._generate_report_use_case.execute(
            GenerateEvaluationReportRequestDTO(
                tenant_id=event.tenant_id,
                interview_session_id=event.session_id,
            )
        )
