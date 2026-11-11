"""Unit tests verifying Outbound Application Ports and Domain Repository Protocols."""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from typing import Any, Self
from uuid import UUID

import pytest

from talent_screener.application.common.ports import TransactionalUnitOfWorkPort
from talent_screener.application.document_intelligence.dtos import (
    CandidateRetrievalEvidenceBundleDTO,
    CompetencyQueryDescriptorDTO,
    DocumentExtractionCommandDTO,
    ExtractedCandidateDossierDTO,
)
from talent_screener.application.document_intelligence.ports import (
    DocumentParserPort,
    HybridVectorSearchPort,
)
from talent_screener.application.live_interview.dtos import (
    CognitiveGraphContextDTO,
    CognitiveGraphResultDTO,
    InterviewInitCommandDTO,
    InterviewInitOutcomeDTO,
)
from talent_screener.application.live_interview.ports import AdaptiveInterviewGraphPort
from talent_screener.domain.contexts.document_intelligence import (
    CandidateProfile,
    CandidateProfileRepositoryPort,
    ExtractedSkillClaim,
    PiiRedactionMap,
    ScreeningBatch,
    ScreeningBatchRepositoryPort,
    SemanticMatchResult,
    SemanticMatchResultRepositoryPort,
    SeniorityEstimate,
)
from talent_screener.domain.contexts.evaluation_analytics import (
    EvaluationReport,
    EvaluationReportRepositoryPort,
)
from talent_screener.domain.contexts.identity_governance import (
    ConsentRecord,
    ConsentRecordRepositoryPort,
    TenantAccount,
    TenantAccountRepositoryPort,
)
from talent_screener.domain.contexts.live_interview import (
    DifficultyLevel,
    InterviewSession,
    InterviewSessionRepositoryPort,
    PromptHash,
    RoutingAction,
    TurnEvaluationScore,
)
from talent_screener.domain.contexts.recruitment import (
    JobRequisition,
    JobRequisitionRepositoryPort,
    RequisitionStatus,
)
from talent_screener.domain.shared import (
    AggregateRoot,
    ConfidenceScore,
    DomainEvent,
    SHA256Hash,
    TenantId,
    generate_uuid7,
)


class _InMemoryUoW:
    def __init__(self) -> None:
        self._aggregates: list[AggregateRoot[Any]] = []
        self.committed: bool = False

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
        self.committed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self._aggregates.clear()

    def register_aggregate(self, aggregate: AggregateRoot[Any]) -> None:
        self._aggregates.append(aggregate)

    def collect_pending_events(self) -> tuple[DomainEvent, ...]:
        events: list[DomainEvent] = []
        for agg in self._aggregates:
            events.extend(agg.pull_domain_events())
        return tuple(events)


class _FakeDocumentParser:
    async def parse_and_extract(
        self, command: DocumentExtractionCommandDTO
    ) -> ExtractedCandidateDossierDTO:
        return ExtractedCandidateDossierDTO(
            anonymized_candidate_token=generate_uuid7(),
            schema_version="1.0.0",
            raw_redacted_text="Built Python services.",
            pii_redaction_map=PiiRedactionMap.from_mapping(
                vault_reference_id=generate_uuid7(),
                token_categories={"<CANDIDATE>": "PERSON"},
            ),
            work_experiences=(),
            projects=(),
            skill_claims=(
                ExtractedSkillClaim(
                    canonical_skill_id="SKILL_LANG_PYTHON",
                    raw_label="Python",
                    source_quote="Built Python services.",
                ),
            ),
            seniority_estimate=SeniorityEstimate(
                level="SENIOR",
                verified_experience_months=60,
                confidence=ConfidenceScore(0.9),
            ),
            extraction_confidence=ConfidenceScore(0.95),
        )


class _FakeHybridSearch:
    async def index_candidate_dossier(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        dossier: ExtractedCandidateDossierDTO,
        sync_version: int,
    ) -> None:
        return None

    async def search_competency_evidence(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        queries: Sequence[CompetencyQueryDescriptorDTO],
        top_k: int = 5,
    ) -> CandidateRetrievalEvidenceBundleDTO:
        return CandidateRetrievalEvidenceBundleDTO(
            tenant_id=tenant_id,
            requisition_id=requisition_id,
            candidate_profile_id=candidate_profile_id,
            competency_evidence=(),
        )

    async def delete_candidate_vectors(
        self, *, tenant_id: TenantId, candidate_profile_id: UUID
    ) -> None:
        return None


class _FakeAdaptiveGraph:
    async def initialize_session_graph(
        self, command: InterviewInitCommandDTO
    ) -> InterviewInitOutcomeDTO:
        q = "Walk me through your distributed locking architecture."
        return InterviewInitOutcomeDTO(
            opening_question=q,
            opening_prompt_hash=PromptHash.from_prompt_text(q),
            initial_competency_id=command.competency_targets[0].competency_id,
            initial_difficulty=DifficultyLevel.L3,
            checkpoint_version="ckpt-1",
        )

    async def execute_cognitive_turn(
        self, context: CognitiveGraphContextDTO
    ) -> CognitiveGraphResultDTO:
        nxt = "How do you prevent split-brain writes during failover?"
        return CognitiveGraphResultDTO(
            turn_evaluation=TurnEvaluationScore(4.2, 4.0, 4.0, 4.1),
            synthetic_likelihood_score=0.05,
            routing_action_taken=RoutingAction.ESCALATE_DIFFICULTY,
            next_difficulty=DifficultyLevel.L4,
            next_interviewer_utterance=nxt,
            next_prompt_hash=PromptHash.from_prompt_text(nxt),
            updated_checkpoint_version="ckpt-2",
            tokens_used=420,
        )

    async def finalize_session_graph(
        self, *, tenant_id: TenantId, session_id: UUID, checkpoint_version: str
    ) -> str:
        return "final-summary"


class _StubJobRepo:
    def save(self, requisition: JobRequisition) -> None: ...
    def get_by_id(self, tenant_id: TenantId, requisition_id: UUID) -> JobRequisition | None:
        return None

    def list_by_tenant(
        self, tenant_id: TenantId, *, status: RequisitionStatus | None = None
    ) -> tuple[JobRequisition, ...]:
        return ()


class _StubBatchRepo:
    def save(self, batch: ScreeningBatch) -> None: ...
    def get_by_id(self, tenant_id: TenantId, batch_id: UUID) -> ScreeningBatch | None:
        return None


class _StubProfileRepo:
    def save(self, profile: CandidateProfile) -> None: ...
    def get_by_id(self, tenant_id: TenantId, profile_id: UUID) -> CandidateProfile | None:
        return None


class _StubMatchRepo:
    def save(self, match_result: SemanticMatchResult) -> None: ...
    def get_by_id(
        self, tenant_id: TenantId, match_result_id: UUID
    ) -> SemanticMatchResult | None:
        return None

    def list_by_requisition(
        self, tenant_id: TenantId, requisition_id: UUID
    ) -> tuple[SemanticMatchResult, ...]:
        return ()


class _StubSessionRepo:
    def save(self, session: InterviewSession) -> None: ...
    def get_by_id(self, tenant_id: TenantId, session_id: UUID) -> InterviewSession | None:
        return None


class _StubReportRepo:
    def save(self, report: EvaluationReport) -> None: ...
    def get_by_id(self, tenant_id: TenantId, report_id: UUID) -> EvaluationReport | None:
        return None

    def get_by_session_id(
        self, tenant_id: TenantId, interview_session_id: UUID
    ) -> EvaluationReport | None:
        return None


class _StubTenantRepo:
    def save(self, tenant: TenantAccount) -> None: ...
    def get_by_id(self, tenant_id: TenantId) -> TenantAccount | None:
        return None


class _StubConsentRepo:
    def save(self, record: ConsentRecord) -> None: ...
    def get_by_id(self, tenant_id: TenantId, record_id: UUID) -> ConsentRecord | None:
        return None

    def get_by_candidate_id(
        self, tenant_id: TenantId, candidate_id: UUID
    ) -> ConsentRecord | None:
        return None


@pytest.mark.unit
def test_outbound_ports_and_repositories_satisfy_runtime_protocols() -> None:
    assert isinstance(_InMemoryUoW(), TransactionalUnitOfWorkPort)
    assert isinstance(_FakeDocumentParser(), DocumentParserPort)
    assert isinstance(_FakeHybridSearch(), HybridVectorSearchPort)
    assert isinstance(_FakeAdaptiveGraph(), AdaptiveInterviewGraphPort)

    assert isinstance(_StubJobRepo(), JobRequisitionRepositoryPort)
    assert isinstance(_StubBatchRepo(), ScreeningBatchRepositoryPort)
    assert isinstance(_StubProfileRepo(), CandidateProfileRepositoryPort)
    assert isinstance(_StubMatchRepo(), SemanticMatchResultRepositoryPort)
    assert isinstance(_StubSessionRepo(), InterviewSessionRepositoryPort)
    assert isinstance(_StubReportRepo(), EvaluationReportRepositoryPort)
    assert isinstance(_StubTenantRepo(), TenantAccountRepositoryPort)
    assert isinstance(_StubConsentRepo(), ConsentRecordRepositoryPort)

    cmd = DocumentExtractionCommandDTO(
        tenant_id=TenantId.generate(),
        batch_id=generate_uuid7(),
        batch_file_id=generate_uuid7(),
        document_stream_uri="s3://quarantine/cv.pdf",
        mime_type="application/pdf",
        expected_sha256=SHA256Hash.from_bytes(b"cv"),
    )
    assert cmd.mime_type == "application/pdf"
