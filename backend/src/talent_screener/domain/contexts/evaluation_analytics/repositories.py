"""Evaluation & Analytics Bounded Context Repository Port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.domain.contexts.evaluation_analytics.aggregates import EvaluationReport
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class EvaluationReportRepositoryPort(Protocol):
    """Persistence contract for EvaluationReport aggregate roots."""

    def save(self, report: EvaluationReport) -> None: ...

    def get_by_id(self, tenant_id: TenantId, report_id: UUID) -> EvaluationReport | None: ...

    def get_by_session_id(
        self,
        tenant_id: TenantId,
        interview_session_id: UUID,
    ) -> EvaluationReport | None: ...

    def get_by_candidate_and_requisition(
        self,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
    ) -> EvaluationReport | None: ...
