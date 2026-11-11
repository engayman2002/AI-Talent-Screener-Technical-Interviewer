"""Reusable deterministic in-memory fakes, spies, and helpers for Application tests."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from talent_screener.application.common.dtos import (
    CryptoShredReceiptDTO,
    FencingLeaseDTO,
    IdempotencyRecordDTO,
    IdempotencyStatus,
    PiiTokenizationOutcomeDTO,
    RealtimeProgressEventDTO,
    TaskDispatchDTO,
    TokenUsageRecordDTO,
    VirusScanOutcomeDTO,
)
from talent_screener.application.common.exceptions import IdempotencyConflictError
from talent_screener.application.document_intelligence.dtos import (
    CandidateRetrievalEvidenceBundleDTO,
    CompetencyQueryDescriptorDTO,
    CompetencyRetrievalEvidenceDTO,
    DocumentExtractionCommandDTO,
    ExtractedCandidateDossierDTO,
    ExtractedProjectDTO,
    ExtractedWorkExperienceDTO,
    RetrievedChunkScoreDTO,
)
from talent_screener.application.live_interview.dtos import (
    AudioTranscriptDTO,
    CognitiveGraphContextDTO,
    CognitiveGraphResultDTO,
    GuardrailEvaluationDTO,
    InterviewInitCommandDTO,
    InterviewInitOutcomeDTO,
)
from talent_screener.composition_root import ApplicationContainer, ExternalAdapterRegistry
from talent_screener.domain.contexts.document_intelligence import (
    ExtractedSkillClaim,
    ImpactMetric,
    PiiRedactionMap,
    SeniorityEstimate,
)
from talent_screener.domain.contexts.live_interview import (
    DifficultyLevel,
    PromptHash,
    RoutingAction,
    TurnEvaluationScore,
)
from talent_screener.domain.shared import (
    ConfidenceScore,
    SHA256Hash,
    TenantId,
    generate_uuid7,
    utc_now,
)


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Execute an async coroutine synchronously in unit tests using stdlib asyncio."""
    return asyncio.run(coro)


class FakeIdempotencyStore:
    """In-memory IdempotencyStorePort supporting replay and mismatch conflict."""

    def __init__(self) -> None:
        self._records: dict[tuple[UUID, str, str], IdempotencyRecordDTO] = {}

    async def check_and_reserve(
        self,
        *,
        tenant_id: TenantId,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> IdempotencyRecordDTO | None:
        key = (tenant_id.value, operation, idempotency_key)
        existing = self._records.get(key)
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise IdempotencyConflictError(
                    "Idempotency key reused with different request payload.",
                )
            return existing
        self._records[key] = IdempotencyRecordDTO(
            tenant_id=tenant_id,
            operation=operation,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            status=IdempotencyStatus.IN_PROGRESS,
        )
        return None

    async def mark_completed(
        self,
        *,
        tenant_id: TenantId,
        operation: str,
        idempotency_key: str,
        resource_id: UUID,
        serialized_result: Mapping[str, object] | None = None,
    ) -> None:
        key = (tenant_id.value, operation, idempotency_key)
        existing = self._records[key]
        self._records[key] = IdempotencyRecordDTO(
            tenant_id=tenant_id,
            operation=operation,
            idempotency_key=idempotency_key,
            payload_hash=existing.payload_hash,
            status=IdempotencyStatus.COMPLETED,
            resource_id=resource_id,
            serialized_result=serialized_result,
        )

    async def release_reservation(
        self,
        *,
        tenant_id: TenantId,
        operation: str,
        idempotency_key: str,
    ) -> None:
        key = (tenant_id.value, operation, idempotency_key)
        rec = self._records.get(key)
        if rec is not None and rec.status is IdempotencyStatus.IN_PROGRESS:
            del self._records[key]


class FakeDistributedLock:
    """In-memory DistributedLockPort issuing monotonic fencing tokens."""

    def __init__(self) -> None:
        self._epoch: int = 0
        self.active_leases: set[UUID] = set()
        self.acquired_count: int = 0
        self.released_count: int = 0

    async def acquire(
        self,
        *,
        tenant_id: TenantId,
        resource_key: str,
        ttl_seconds: int = 30,
    ) -> FencingLeaseDTO:
        self._epoch += 1
        self.acquired_count += 1
        lease_id = generate_uuid7()
        self.active_leases.add(lease_id)
        return FencingLeaseDTO(
            tenant_id=tenant_id,
            resource_key=resource_key,
            fencing_token=self._epoch,
            lease_id=lease_id,
            expires_at=utc_now(),
        )

    async def release(self, *, lease: FencingLeaseDTO) -> None:
        self.active_leases.discard(lease.lease_id)
        self.released_count += 1


class FakeObjectStorage:
    """In-memory ObjectStoragePort storing tenant-scoped binary payloads."""

    def __init__(self) -> None:
        self.blobs: dict[tuple[UUID, str], bytes] = {}

    async def generate_presigned_upload_url(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
        content_sha256: SHA256Hash,
        content_type: str,
        expires_in_seconds: int = 900,
    ) -> str:
        return f"https://storage.local/{tenant_id}/{object_key}?sha256={content_sha256}"

    async def put_bytes(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
        payload: bytes,
        content_type: str,
    ) -> SHA256Hash:
        self.blobs[(tenant_id.value, object_key)] = payload
        return SHA256Hash.from_bytes(payload)

    async def get_bytes(self, *, tenant_id: TenantId, object_key: str) -> bytes:
        return self.blobs.get((tenant_id.value, object_key), b"%PDF-1.7 fake cv content")

    async def delete_object(self, *, tenant_id: TenantId, object_key: str) -> None:
        self.blobs.pop((tenant_id.value, object_key), None)


class FakeVirusScanner:
    """Configurable VirusScannerPort flagging EICAR or configured infected filenames."""

    def __init__(self) -> None:
        self.infected_filenames: set[str] = set()

    async def scan_bytes(
        self,
        *,
        tenant_id: TenantId,
        filename: str,
        payload: bytes,
        expected_sha256: SHA256Hash,
    ) -> VirusScanOutcomeDTO:
        if filename in self.infected_filenames or b"EICAR" in payload:
            return VirusScanOutcomeDTO(
                is_clean=False,
                detected_signature="EICAR-TEST-SIGNATURE",
                verified_mime_type="application/octet-stream",
                verified_sha256=expected_sha256,
                reason_code="MALWARE_DETECTED",
            )
        return VirusScanOutcomeDTO(
            is_clean=True,
            detected_signature=None,
            verified_mime_type="application/pdf",
            verified_sha256=expected_sha256,
        )


class FakePiiVault:
    """In-memory PiiVaultPort simulating PII tokenization and GDPR crypto-shredding."""

    def __init__(self) -> None:
        self.shredded_candidates: list[tuple[UUID, UUID, str]] = []
        self._vault_secrets: dict[UUID, dict[str, str]] = {}

    async def tokenize_and_vault(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
        raw_text: str,
    ) -> PiiTokenizationOutcomeDTO:
        vault_id = generate_uuid7()
        self._vault_secrets[vault_id] = {"<CANDIDATE_EMAIL>": "candidate@example.com"}
        return PiiTokenizationOutcomeDTO(
            anonymized_text=raw_text,
            pii_redaction_map=PiiRedactionMap.from_mapping(
                vault_reference_id=vault_id,
                token_categories={"<CANDIDATE_EMAIL>": "EMAIL"},
            ),
            vault_reference_id=vault_id,
        )

    async def detokenize(
        self, *, tenant_id: TenantId, vault_reference_id: UUID
    ) -> Mapping[str, str]:
        return self._vault_secrets.get(vault_reference_id, {})

    async def crypto_shred(
        self,
        *,
        tenant_id: TenantId,
        candidate_id: UUID,
        reason: str,
    ) -> CryptoShredReceiptDTO:
        self.shredded_candidates.append((tenant_id.value, candidate_id, reason))
        return CryptoShredReceiptDTO(
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            shredded_at=utc_now(),
            already_shredded=False,
        )


class SpyTaskQueueDispatcher:
    """Spy TaskQueueDispatcherPort recording dispatched background jobs."""

    def __init__(self) -> None:
        self.dispatched_tasks: list[TaskDispatchDTO] = []

    async def dispatch(self, task: TaskDispatchDTO) -> str:
        self.dispatched_tasks.append(task)
        return f"task-{len(self.dispatched_tasks)}"

    async def dispatch_batch(self, tasks: Sequence[TaskDispatchDTO]) -> tuple[str, ...]:
        ids: list[str] = []
        for task in tasks:
            ids.append(await self.dispatch(task))
        return tuple(ids)


class SpyRealtimeProgressPublisher:
    """Spy RealtimeProgressPublisherPort capturing emitted SSE/WS telemetry events."""

    def __init__(self) -> None:
        self.events: list[RealtimeProgressEventDTO] = []

    async def publish_progress(self, event: RealtimeProgressEventDTO) -> None:
        self.events.append(event)


class _DummyTraceSpan(AbstractContextManager["_DummyTraceSpan"]):
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, object] = {}

    def __enter__(self) -> _DummyTraceSpan:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class SpyObservabilityTracer:
    """Spy ObservabilityTracerPort recording spans, metrics, and token usage."""

    def __init__(self) -> None:
        self.spans: list[str] = []
        self.token_usages: list[TokenUsageRecordDTO] = []
        self.metrics: list[tuple[str, float]] = []

    def start_span(
        self,
        *,
        name: str,
        tenant_id: TenantId,
        correlation_id: str,
        attributes: Mapping[str, object] | None = None,
    ) -> _DummyTraceSpan:
        self.spans.append(name)
        return _DummyTraceSpan(name)

    def record_token_usage(self, usage: TokenUsageRecordDTO) -> None:
        self.token_usages.append(usage)

    def record_metric(
        self,
        *,
        name: str,
        value: float,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        self.metrics.append((name, value))


class FakeDocumentParser:
    """Deterministic DocumentParserPort emitting grounded CV dossier DTOs."""

    def __init__(self, *, applied_python_months: int = 48) -> None:
        self.applied_python_months = applied_python_months

    async def parse_and_extract(
        self, command: DocumentExtractionCommandDTO
    ) -> ExtractedCandidateDossierDTO:
        raw_text = (
            "Senior Distributed Systems Engineer. Built Python asyncio microservices handling "
            "25k requests/sec with PostgreSQL and Kubernetes. Reduced P99 latency by 40%."
        )
        return ExtractedCandidateDossierDTO(
            anonymized_candidate_token=generate_uuid7(),
            schema_version="1.0.0",
            raw_redacted_text=raw_text,
            pii_redaction_map=PiiRedactionMap.from_mapping(
                vault_reference_id=generate_uuid7(),
                token_categories={"<CANDIDATE_NAME>": "PERSON_NAME"},
            ),
            work_experiences=(
                ExtractedWorkExperienceDTO(
                    role_title="Senior Distributed Systems Engineer",
                    normalized_role="BACKEND_ENGINEER",
                    company_token="COMPANY_X",
                    start_year=2021,
                    start_month=1,
                    end_year=2024,
                    end_month=12,
                ),
            ),
            projects=(
                ExtractedProjectDTO(
                    project_name="Distributed Event Gateway",
                    scale_tier="HIGH_THROUGHPUT_PRODUCTION",
                    summary="Built Python asyncio microservices handling 25k requests/sec.",
                    source_quote="Built Python asyncio microservices handling 25k requests/sec",
                    impact_metrics=(
                        ImpactMetric(
                            metric_type="LATENCY_REDUCTION",
                            numeric_value=40.0,
                            unit="PERCENT",
                            source_quote="Reduced P99 latency by 40%.",
                        ),
                    ),
                ),
            ),
            skill_claims=(
                ExtractedSkillClaim(
                    canonical_skill_id="SKILL_LANG_PYTHON",
                    raw_label="Python",
                    source_quote="Built Python asyncio microservices handling 25k requests/sec",
                    claimed_depth=4,
                    applied_months=self.applied_python_months,
                    recency_months=0,
                    confidence=ConfidenceScore(0.95),
                ),
                ExtractedSkillClaim(
                    canonical_skill_id="SKILL_DISTRIBUTED_SYSTEMS",
                    raw_label="Distributed Systems",
                    source_quote="Senior Distributed Systems Engineer",
                    claimed_depth=4,
                    applied_months=48,
                    recency_months=0,
                    confidence=ConfidenceScore(0.92),
                ),
            ),
            seniority_estimate=SeniorityEstimate(
                level="SENIOR",
                verified_experience_months=48,
                confidence=ConfidenceScore(0.93),
            ),
            extraction_confidence=ConfidenceScore(0.95),
        )


class FakeHybridVectorSearch:
    """In-memory HybridVectorSearchPort tracking indexed and purged candidate vectors."""

    def __init__(self) -> None:
        self.indexed_profiles: set[tuple[UUID, UUID]] = set()
        self.deleted_profiles: list[tuple[UUID, UUID]] = []
        self.default_dense: float = 0.92
        self.default_sparse: float = 0.88

    async def index_candidate_dossier(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        dossier: ExtractedCandidateDossierDTO,
        sync_version: int,
    ) -> None:
        self.indexed_profiles.add((tenant_id.value, candidate_profile_id))

    async def search_competency_evidence(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        queries: Sequence[CompetencyQueryDescriptorDTO],
        top_k: int = 5,
    ) -> CandidateRetrievalEvidenceBundleDTO:
        evidence = tuple(
            CompetencyRetrievalEvidenceDTO(
                competency_id=q.competency_id,
                retrieved_chunks=(
                    RetrievedChunkScoreDTO(
                        chunk_id=f"chunk-{q.canonical_skill_id}-1",
                        parent_entry_id="work-1",
                        dense_similarity=self.default_dense,
                        sparse_bm25_score=self.default_sparse,
                        recency_months_delta=0,
                        evidentiary_depth=4,
                        verbatim_excerpt="Built high-throughput distributed services.",
                    ),
                ),
            )
            for q in queries
        )
        return CandidateRetrievalEvidenceBundleDTO(
            tenant_id=tenant_id,
            requisition_id=requisition_id,
            candidate_profile_id=candidate_profile_id,
            competency_evidence=evidence,
        )

    async def delete_candidate_vectors(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
    ) -> None:
        self.indexed_profiles.discard((tenant_id.value, candidate_profile_id))
        self.deleted_profiles.append((tenant_id.value, candidate_profile_id))


class FakeAdaptiveInterviewGraph:
    """Scriptable AdaptiveInterviewGraphPort returning deterministic turn outcomes."""

    def __init__(self) -> None:
        self.next_routing_action: RoutingAction = RoutingAction.ESCALATE_DIFFICULTY
        self.next_difficulty: DifficultyLevel = DifficultyLevel.L4
        self.next_score: TurnEvaluationScore = TurnEvaluationScore(4.5, 4.4, 4.2, 4.3, 0.05)
        self.synthetic_score: float = 0.05
        self.tokens_per_turn: int = 450

    async def initialize_session_graph(
        self, command: InterviewInitCommandDTO
    ) -> InterviewInitOutcomeDTO:
        skill_id = command.competency_targets[0].canonical_skill_id
        q = f"Tell me about your production architecture for {skill_id}."
        return InterviewInitOutcomeDTO(
            opening_question=q,
            opening_prompt_hash=PromptHash.from_prompt_text(q),
            initial_competency_id=command.competency_targets[0].competency_id,
            initial_difficulty=DifficultyLevel.L3,
            checkpoint_version="ckpt-0",
        )

    async def execute_cognitive_turn(
        self, context: CognitiveGraphContextDTO
    ) -> CognitiveGraphResultDTO:
        nxt = "How does your lease design tolerate network partitions and clock drift?"
        return CognitiveGraphResultDTO(
            turn_evaluation=self.next_score,
            synthetic_likelihood_score=self.synthetic_score,
            routing_action_taken=self.next_routing_action,
            next_difficulty=self.next_difficulty,
            next_interviewer_utterance=nxt,
            next_prompt_hash=PromptHash.from_prompt_text(nxt),
            updated_checkpoint_version=f"ckpt-{context.turn_sequence_number}",
            tokens_used=self.tokens_per_turn,
        )

    async def finalize_session_graph(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        checkpoint_version: str,
    ) -> str:
        return "Synthesized strong distributed systems mastery across all probed competencies."


class FakeAudioStt:
    """Fake AudioSttPort converting binary audio to transcript."""

    async def transcribe_audio(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        audio_bytes: bytes,
        sample_rate_hz: int = 16_000,
    ) -> AudioTranscriptDTO:
        return AudioTranscriptDTO(
            transcript_text="We used monotonic fencing tokens in PostgreSQL.",
            duration_ms=4200,
            speech_cadence_wpm=132.0,
            pause_entropy=0.65,
        )


class FakeAudioTts:
    """Fake AudioTtsPort returning synthetic PCM frame bytes."""

    async def synthesize_speech(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        text: str,
        voice_id: str = "interviewer_default",
    ) -> bytes:
        return b"OPUS_AUDIO_BYTES"


class FakePromptGuardrail:
    """Configurable PromptGuardrailPort detecting prompt-injection keywords."""

    def __init__(self) -> None:
        self.blocked_phrases: tuple[str, ...] = ("ignore rubric", "system override")
        self.suspicious_phrases: tuple[str, ...] = ("json output",)

    async def evaluate_candidate_input(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        turn_number: int,
        raw_utterance: str,
    ) -> GuardrailEvaluationDTO:
        lower = raw_utterance.lower()
        if any(phrase in lower for phrase in self.blocked_phrases):
            return GuardrailEvaluationDTO(
                risk_score=0.92,
                sanitized_text="[REDACTED_ADVERSARIAL_INPUT]",
                is_suspicious=True,
                is_blocked=True,
                reason_code="PROMPT_INJECTION_BLOCKED",
            )
        if any(phrase in lower for phrase in self.suspicious_phrases):
            return GuardrailEvaluationDTO(
                risk_score=0.45,
                sanitized_text=raw_utterance,
                is_suspicious=True,
                is_blocked=False,
                reason_code="SUSPICIOUS_META_FRAMING",
            )
        return GuardrailEvaluationDTO(
            risk_score=0.02,
            sanitized_text=raw_utterance,
            is_suspicious=False,
            is_blocked=False,
        )


class InMemoryTokenBudgetEnforcer:
    """In-memory TokenBudgetEnforcerPort enforcing per-session FinOps ceiling."""

    def __init__(self) -> None:
        self.usage_by_session: dict[UUID, int] = {}

    async def ensure_within_budget(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        requested_tokens: int,
        session_limit: int,
    ) -> None:
        current = self.usage_by_session.get(session_id, 0)
        if current + requested_tokens > session_limit:
            from talent_screener.application.common.exceptions import QuotaExceededError

            raise QuotaExceededError(
                f"Session token ceiling {session_limit} exceeded.",
                code="TOKEN_BUDGET_EXCEEDED",
            )

    async def record_consumption(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        tokens_used: int,
    ) -> int:
        updated = self.usage_by_session.get(session_id, 0) + tokens_used
        self.usage_by_session[session_id] = updated
        return updated


@dataclass(slots=True)
class ApplicationTestHarness:
    """Convenient bundle exposing wired ApplicationContainer and test doubles."""

    container: ApplicationContainer
    idempotency_store: FakeIdempotencyStore
    distributed_lock: FakeDistributedLock
    object_storage: FakeObjectStorage
    virus_scanner: FakeVirusScanner
    pii_vault: FakePiiVault
    task_dispatcher: SpyTaskQueueDispatcher
    progress_publisher: SpyRealtimeProgressPublisher
    tracer: SpyObservabilityTracer
    document_parser: FakeDocumentParser
    vector_search: FakeHybridVectorSearch
    interview_graph: FakeAdaptiveInterviewGraph
    audio_stt: FakeAudioStt
    audio_tts: FakeAudioTts
    prompt_guardrail: FakePromptGuardrail
    token_budget: InMemoryTokenBudgetEnforcer


def build_test_harness() -> ApplicationTestHarness:
    """Construct a fully wired ApplicationContainer backed by deterministic in-memory doubles."""
    idem = FakeIdempotencyStore()
    lock = FakeDistributedLock()
    storage = FakeObjectStorage()
    scanner = FakeVirusScanner()
    vault = FakePiiVault()
    tasks = SpyTaskQueueDispatcher()
    progress = SpyRealtimeProgressPublisher()
    tracer = SpyObservabilityTracer()
    parser = FakeDocumentParser()
    vectors = FakeHybridVectorSearch()
    graph = FakeAdaptiveInterviewGraph()
    stt = FakeAudioStt()
    tts = FakeAudioTts()
    guard = FakePromptGuardrail()
    budget = InMemoryTokenBudgetEnforcer()

    adapters = ExternalAdapterRegistry(
        idempotency_store=idem,
        distributed_lock=lock,
        object_storage=storage,
        virus_scanner=scanner,
        pii_vault=vault,
        task_dispatcher=tasks,
        progress_publisher=progress,
        observability_tracer=tracer,
        document_parser=parser,
        hybrid_vector_search=vectors,
        adaptive_interview_graph=graph,
        audio_stt=stt,
        audio_tts=tts,
        prompt_guardrail=guard,
        token_budget_enforcer=budget,
    )
    container = ApplicationContainer(adapters=adapters)
    return ApplicationTestHarness(
        container=container,
        idempotency_store=idem,
        distributed_lock=lock,
        object_storage=storage,
        virus_scanner=scanner,
        pii_vault=vault,
        task_dispatcher=tasks,
        progress_publisher=progress,
        tracer=tracer,
        document_parser=parser,
        vector_search=vectors,
        interview_graph=graph,
        audio_stt=stt,
        audio_tts=tts,
        prompt_guardrail=guard,
        token_budget=budget,
    )
