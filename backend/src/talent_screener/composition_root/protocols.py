"""Pure Dependency Injection container and wiring protocols for the Composition Root."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.common.ports.outbound import (
    DomainEventBusPort,
    TransactionalUnitOfWorkPort,
)
from talent_screener.application.document_intelligence.ports.inbound import (
    ExecuteHybridMatchingPort,
    GetScreeningBatchStatusPort,
    InitiateScreeningBatchPort,
    ProcessDocumentIngestionTaskPort,
)
from talent_screener.application.evaluation_analytics.ports.inbound import (
    BuildCandidateComparisonMatrixPort,
    GenerateEvaluationReportPort,
    RecordRecruiterOverridePort,
)
from talent_screener.application.identity_governance.ports.inbound import (
    ExecuteCandidateErasurePort,
    ProvisionTenantAccountPort,
    RecordCandidateConsentPort,
    RevokeCandidateConsentPort,
)
from talent_screener.application.live_interview.ports.inbound import (
    CompleteInterviewSessionPort,
    InitializeInterviewSessionPort,
    ProcessCandidateTurnPort,
    RecordClientTelemetryPort,
    TerminateInterviewEarlyPort,
)
from talent_screener.application.recruitment.ports.inbound import (
    ArchiveJobRequisitionPort,
    CreateJobRequisitionPort,
    GetJobRequisitionPort,
    LockJobRequisitionRubricPort,
    PublishJobRequisitionPort,
    UpdateJobRubricPort,
)


@runtime_checkable
class UseCaseCatalogPort(Protocol):
    """Read-only registry exposing wired Inbound Driving Ports across all contexts."""

    @property
    def create_job_requisition(self) -> CreateJobRequisitionPort: ...

    @property
    def publish_job_requisition(self) -> PublishJobRequisitionPort: ...

    @property
    def update_job_rubric(self) -> UpdateJobRubricPort: ...

    @property
    def lock_job_rubric(self) -> LockJobRequisitionRubricPort: ...

    @property
    def archive_job_requisition(self) -> ArchiveJobRequisitionPort: ...

    @property
    def get_job_requisition(self) -> GetJobRequisitionPort: ...

    @property
    def initiate_screening_batch(self) -> InitiateScreeningBatchPort: ...

    @property
    def process_document_task(self) -> ProcessDocumentIngestionTaskPort: ...

    @property
    def execute_hybrid_matching(self) -> ExecuteHybridMatchingPort: ...

    @property
    def get_screening_batch_status(self) -> GetScreeningBatchStatusPort: ...

    @property
    def initialize_interview_session(self) -> InitializeInterviewSessionPort: ...

    @property
    def process_candidate_turn(self) -> ProcessCandidateTurnPort: ...

    @property
    def record_client_telemetry(self) -> RecordClientTelemetryPort: ...

    @property
    def terminate_interview_early(self) -> TerminateInterviewEarlyPort: ...

    @property
    def complete_interview_session(self) -> CompleteInterviewSessionPort: ...

    @property
    def generate_evaluation_report(self) -> GenerateEvaluationReportPort: ...

    @property
    def record_recruiter_override(self) -> RecordRecruiterOverridePort: ...

    @property
    def build_comparison_matrix(self) -> BuildCandidateComparisonMatrixPort: ...

    @property
    def provision_tenant_account(self) -> ProvisionTenantAccountPort: ...

    @property
    def record_candidate_consent(self) -> RecordCandidateConsentPort: ...

    @property
    def revoke_candidate_consent(self) -> RevokeCandidateConsentPort: ...

    @property
    def execute_candidate_erasure(self) -> ExecuteCandidateErasurePort: ...


@runtime_checkable
class EventChoreographyWiringPort(Protocol):
    """Contract for binding cross-context Domain Event Handlers onto a DomainEventBusPort."""

    def wire_event_handlers(self, event_bus: DomainEventBusPort) -> None: ...


@runtime_checkable
class ApplicationContainerPort(UseCaseCatalogPort, EventChoreographyWiringPort, Protocol):
    """Full Composition Root contract providing wired use cases, UoW, and event bus."""

    @property
    def unit_of_work(self) -> TransactionalUnitOfWorkPort: ...

    @property
    def event_bus(self) -> DomainEventBusPort: ...
