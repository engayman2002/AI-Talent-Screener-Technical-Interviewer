"""In-memory reference Unit of Work and tenant-scoped repositories."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self
from uuid import UUID

from talent_screener.application.common.ports.outbound import DomainEventBusPort
from talent_screener.domain.contexts.document_intelligence import (
    CandidateProfile,
    ScreeningBatch,
    SemanticMatchResult,
)
from talent_screener.domain.contexts.evaluation_analytics import EvaluationReport
from talent_screener.domain.contexts.identity_governance import (
    ConsentRecord,
    TenantAccount,
)
from talent_screener.domain.contexts.live_interview import InterviewSession
from talent_screener.domain.contexts.recruitment import (
    JobRequisition,
    RequisitionStatus,
)
from talent_screener.domain.shared.base import AggregateRoot, DomainEvent
from talent_screener.domain.shared.value_objects import TenantId


class InMemoryUnitOfWork:
    """Reference TransactionalUnitOfWorkPort dispatching aggregate events on commit."""

    def __init__(self, *, event_bus: DomainEventBusPort | None = None) -> None:
        self._event_bus = event_bus
        self._active_tenant_id: TenantId | None = None
        self._aggregates: list[AggregateRoot[Any]] = []
        self._dispatched_events: list[DomainEvent] = []
        self.commit_count: int = 0
        self.rollback_count: int = 0

    @property
    def dispatched_events(self) -> tuple[DomainEvent, ...]:
        return tuple(self._dispatched_events)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()

    async def begin(self, tenant_id: TenantId) -> None:
        self._active_tenant_id = tenant_id
        self._aggregates.clear()

    async def commit(self) -> None:
        events = list(self.collect_pending_events())
        self._aggregates.clear()
        self.commit_count += 1
        if events:
            self._dispatched_events.extend(events)
            if self._event_bus is not None:
                await self._event_bus.publish_all(events)

    async def rollback(self) -> None:
        for agg in self._aggregates:
            agg.pull_domain_events()
        self._aggregates.clear()
        self.rollback_count += 1

    def register_aggregate(self, aggregate: AggregateRoot[Any]) -> None:
        if aggregate not in self._aggregates:
            self._aggregates.append(aggregate)

    def collect_pending_events(self) -> tuple[DomainEvent, ...]:
        collected: list[DomainEvent] = []
        for agg in self._aggregates:
            collected.extend(agg.pull_domain_events())
        return tuple(collected)


class InMemoryJobRequisitionRepository:
    """Tenant-isolated in-memory repository for JobRequisition aggregates."""

    def __init__(self) -> None:
        self._store: dict[tuple[UUID, UUID], JobRequisition] = {}

    def save(self, requisition: JobRequisition) -> None:
        self._store[(requisition.tenant_id.value, requisition.id)] = requisition

    def get_by_id(self, tenant_id: TenantId, requisition_id: UUID) -> JobRequisition | None:
        return self._store.get((tenant_id.value, requisition_id))

    def list_by_tenant(
        self,
        tenant_id: TenantId,
        *,
        status: RequisitionStatus | None = None,
    ) -> tuple[JobRequisition, ...]:
        items = [
            req
            for (tid, _), req in self._store.items()
            if tid == tenant_id.value and (status is None or req.status is status)
        ]
        return tuple(items)


class InMemoryScreeningBatchRepository:
    """Tenant-isolated in-memory repository for ScreeningBatch aggregates."""

    def __init__(self) -> None:
        self._store: dict[tuple[UUID, UUID], ScreeningBatch] = {}

    def save(self, batch: ScreeningBatch) -> None:
        self._store[(batch.tenant_id.value, batch.id)] = batch

    def get_by_id(self, tenant_id: TenantId, batch_id: UUID) -> ScreeningBatch | None:
        return self._store.get((tenant_id.value, batch_id))


class InMemoryCandidateProfileRepository:
    """Tenant-isolated in-memory repository for CandidateProfile aggregates."""

    def __init__(self) -> None:
        self._store: dict[tuple[UUID, UUID], CandidateProfile] = {}

    def save(self, profile: CandidateProfile) -> None:
        self._store[(profile.tenant_id.value, profile.id)] = profile

    def get_by_id(self, tenant_id: TenantId, profile_id: UUID) -> CandidateProfile | None:
        return self._store.get((tenant_id.value, profile_id))


class InMemorySemanticMatchResultRepository:
    """Tenant-isolated in-memory repository for SemanticMatchResult aggregates."""

    def __init__(self) -> None:
        self._store: dict[tuple[UUID, UUID], SemanticMatchResult] = {}

    def save(self, match_result: SemanticMatchResult) -> None:
        self._store[(match_result.tenant_id.value, match_result.id)] = match_result

    def get_by_id(
        self, tenant_id: TenantId, match_result_id: UUID
    ) -> SemanticMatchResult | None:
        return self._store.get((tenant_id.value, match_result_id))

    def get_by_candidate_and_requisition(
        self,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
    ) -> SemanticMatchResult | None:
        for (tid, _), res in self._store.items():
            if (
                tid == tenant_id.value
                and res.requisition_id == requisition_id
                and res.candidate_profile_id == candidate_profile_id
            ):
                return res
        return None

    def list_by_requisition(
        self, tenant_id: TenantId, requisition_id: UUID
    ) -> tuple[SemanticMatchResult, ...]:
        return tuple(
            res
            for (tid, _), res in self._store.items()
            if tid == tenant_id.value and res.requisition_id == requisition_id
        )


class InMemoryInterviewSessionRepository:
    """Tenant-isolated in-memory repository for InterviewSession aggregates."""

    def __init__(self) -> None:
        self._store: dict[tuple[UUID, UUID], InterviewSession] = {}

    def save(self, session: InterviewSession) -> None:
        self._store[(session.tenant_id.value, session.id)] = session

    def get_by_id(self, tenant_id: TenantId, session_id: UUID) -> InterviewSession | None:
        return self._store.get((tenant_id.value, session_id))


class InMemoryEvaluationReportRepository:
    """Tenant-isolated in-memory repository for EvaluationReport aggregates."""

    def __init__(self) -> None:
        self._store: dict[tuple[UUID, UUID], EvaluationReport] = {}

    def save(self, report: EvaluationReport) -> None:
        self._store[(report.tenant_id.value, report.id)] = report

    def get_by_id(self, tenant_id: TenantId, report_id: UUID) -> EvaluationReport | None:
        return self._store.get((tenant_id.value, report_id))

    def get_by_session_id(
        self, tenant_id: TenantId, interview_session_id: UUID
    ) -> EvaluationReport | None:
        for (tid, _), rep in self._store.items():
            if tid == tenant_id.value and rep.interview_session_id == interview_session_id:
                return rep
        return None

    def get_by_candidate_and_requisition(
        self,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
    ) -> EvaluationReport | None:
        for (tid, _), rep in self._store.items():
            if (
                tid == tenant_id.value
                and rep.requisition_id == requisition_id
                and rep.candidate_profile_id == candidate_profile_id
            ):
                return rep
        return None


class InMemoryTenantAccountRepository:
    """In-memory repository for TenantAccount aggregates."""

    def __init__(self) -> None:
        self._store: dict[UUID, TenantAccount] = {}

    def save(self, tenant: TenantAccount) -> None:
        self._store[tenant.id.value] = tenant

    def get_by_id(self, tenant_id: TenantId) -> TenantAccount | None:
        return self._store.get(tenant_id.value)

    def get_by_slug(self, organization_slug: str) -> TenantAccount | None:
        slug_norm = organization_slug.strip().lower()
        for tenant in self._store.values():
            if tenant.organization_slug == slug_norm:
                return tenant
        return None


class InMemoryConsentRecordRepository:
    """Tenant-isolated in-memory repository for ConsentRecord aggregates."""

    def __init__(self) -> None:
        self._store: dict[tuple[UUID, UUID], ConsentRecord] = {}

    def save(self, record: ConsentRecord) -> None:
        self._store[(record.tenant_id.value, record.id)] = record

    def get_by_id(self, tenant_id: TenantId, record_id: UUID) -> ConsentRecord | None:
        return self._store.get((tenant_id.value, record_id))

    def get_by_candidate_id(
        self, tenant_id: TenantId, candidate_id: UUID
    ) -> ConsentRecord | None:
        for (tid, _), rec in self._store.items():
            if tid == tenant_id.value and rec.candidate_id == candidate_id:
                return rec
        return None
