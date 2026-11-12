"""Composition Root Dependency Injection container wiring Ports to Adapters (ADR-002)."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from talent_screener.application.common.ports.outbound import (
    DistributedLockPort,
    DomainEventBusPort,
    IdempotencyStorePort,
    ObjectStoragePort,
    ObservabilityTracerPort,
    PiiVaultPort,
    RealtimeProgressPublisherPort,
    TaskQueueDispatcherPort,
    TransactionalUnitOfWorkPort,
    VirusScannerPort,
)
from talent_screener.application.document_intelligence.ports.outbound import (
    DocumentParserPort,
    HybridVectorSearchPort,
)
from talent_screener.application.document_intelligence.use_cases import (
    ExecuteHybridMatchingUseCase,
    GetScreeningBatchStatusUseCase,
    InitiateScreeningBatchUseCase,
    ProcessDocumentIngestionTaskUseCase,
)
from talent_screener.application.evaluation_analytics.event_handlers import (
    TriggerEvaluationReportOnInterviewCompletedHandler,
)
from talent_screener.application.evaluation_analytics.use_cases import (
    BuildCandidateComparisonMatrixUseCase,
    GenerateEvaluationReportUseCase,
    RecordRecruiterOverrideUseCase,
)
from talent_screener.application.identity_governance.use_cases import (
    ExecuteCandidateErasureUseCase,
    ProvisionTenantAccountUseCase,
    RecordCandidateConsentUseCase,
    RevokeCandidateConsentUseCase,
)
from talent_screener.application.live_interview.ports.outbound import (
    AdaptiveInterviewGraphPort,
    AudioSttPort,
    AudioTtsPort,
    PromptGuardrailPort,
    TokenBudgetEnforcerPort,
)
from talent_screener.application.live_interview.use_cases import (
    CompleteInterviewSessionUseCase,
    InitializeInterviewSessionUseCase,
    ProcessCandidateTurnUseCase,
    RecordClientTelemetryUseCase,
    TerminateInterviewEarlyUseCase,
)
from talent_screener.application.recruitment.event_handlers import (
    LockRubricOnBatchOrInterviewStartedHandler,
)
from talent_screener.application.recruitment.use_cases import (
    ArchiveJobRequisitionUseCase,
    CreateJobRequisitionUseCase,
    GetJobRequisitionUseCase,
    LockJobRequisitionRubricUseCase,
    PublishJobRequisitionUseCase,
    UpdateJobRubricUseCase,
)
from talent_screener.domain.contexts.document_intelligence import (
    CandidateProfileRepositoryPort,
    ScreeningBatchCreatedEvent,
    ScreeningBatchRepositoryPort,
    SemanticMatchResultRepositoryPort,
)
from talent_screener.domain.contexts.evaluation_analytics import (
    EvaluationReportRepositoryPort,
)
from talent_screener.domain.contexts.identity_governance import (
    ConsentRecordRepositoryPort,
    TenantAccountRepositoryPort,
)
from talent_screener.domain.contexts.live_interview import (
    InterviewSessionCompletedEvent,
    InterviewSessionRepositoryPort,
    InterviewSessionStartedEvent,
)
from talent_screener.domain.contexts.recruitment import JobRequisitionRepositoryPort
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.ai_adapters.audio_adapters import (
    StreamingAudioSttAdapter,
    StreamingAudioTtsAdapter,
    SttDriverProtocol,
    TtsDriverProtocol,
)
from talent_screener.infrastructure.ai_adapters.langchain_parser import (
    LangChainDocumentParserAdapter,
    StructuredExtractionRunnableProtocol,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview import (
    InterviewStateCheckpointerProtocol,
    InterviewSynthesisModelProtocol,
    LangGraphAdaptiveInterviewAdapter,
    QuestionGenerationModelProtocol,
    SlidingWindowTokenBudgetEnforcerAdapter,
    SocraticProbeModelProtocol,
    TurnEvaluationModelProtocol,
)
from talent_screener.infrastructure.ai_adapters.live_llm_provider import (
    OpenAiCompatibleJsonLlmClient,
    build_live_llm_client_from_env,
)
from talent_screener.infrastructure.ai_adapters.vector_store import (
    DenseEmbeddingClientProtocol,
    HybridVectorSearchAdapter,
)
from talent_screener.infrastructure.messaging import (
    BatchIngestionWorkerCoordinator,
    FencingTokenDistributedLockAdapter,
    InMemoryDeadLetterQueue,
    InMemoryDomainEventBus,
    InMemoryTaskQueueDispatcher,
    PriorityReservedTokenBucketLimiter,
    QueueDepthWorkerAutoscaler,
    ReplayableSseProgressBroker,
    ResilientTaskExecutor,
    StatelessWebSocketPubSubBackplane,
    WorkerPoolScalingPolicy,
)
from talent_screener.infrastructure.observability import (
    InMemoryObservabilityTracer,
    MultiTenantFinOpsController,
    RubricDriftMonitor,
    StructuredOpenTelemetryTracerAdapter,
)
from talent_screener.infrastructure.persistence import (
    CrossStoreAntiEntropyReconciler,
    DatabaseConnectionConfig,
    InMemoryCandidateProfileRepository,
    InMemoryConsentRecordRepository,
    InMemoryEvaluationReportRepository,
    InMemoryHybridVectorStoreBackend,
    InMemoryIdempotencyStore,
    InMemoryInterviewSessionRepository,
    InMemoryJobRequisitionRepository,
    InMemoryScreeningBatchRepository,
    InMemorySemanticMatchResultRepository,
    InMemoryTenantAccountRepository,
    InMemoryUnitOfWork,
    QuantizedHybridVectorStoreBackend,
    SqlCandidatePiiVaultStore,
    SqlCandidateProfileRepository,
    SqlConsentRecordRepository,
    SqlDatabaseManager,
    SqlEvaluationReportRepository,
    SqlIdempotencyStoreAdapter,
    SqlInterviewSessionRepository,
    SqlJobRequisitionRepository,
    SqlScreeningBatchRepository,
    SqlSemanticMatchResultRepository,
    SqlTenantAccountRepository,
    SqlTransactionalUnitOfWork,
    TenantScopedObjectStorageAdapter,
    TransactionalOutboxRelayWorker,
    TwoTierFencedStateCheckpointer,
    VectorStoreBackendProtocol,
)
from talent_screener.infrastructure.security import (
    AdversarialPromptGuardrailAdapter,
    CryptographicPiiVaultAdapter,
    DefenseInDepthFirewall,
    EnvelopeCipherProtocol,
    SignatureAndHeuristicVirusScannerAdapter,
)
from talent_screener.presentation.middleware import (
    BearerTokenAuthenticator,
    ExceptionToProblemDetailsTranslator,
    IdempotencyMiddleware,
    TenantRateLimiter,
)
from talent_screener.presentation.rest import (
    DocumentIntelligenceController,
    EvaluationController,
    IdentityController,
    LiveInterviewController,
    OpsController,
    RecruitmentController,
    RestApiRouter,
)
from talent_screener.presentation.server import AsgiAppFn, create_asgi_app
from talent_screener.presentation.websocket import (
    WebSocketClusterHooks,
    WebSocketInterviewGateway,
)


@dataclass(frozen=True, slots=True)
class RepositoryRegistry:
    """Bundle of repository port implementations wired into the container."""

    requisition_repo: JobRequisitionRepositoryPort
    batch_repo: ScreeningBatchRepositoryPort
    profile_repo: CandidateProfileRepositoryPort
    match_repo: SemanticMatchResultRepositoryPort
    session_repo: InterviewSessionRepositoryPort
    report_repo: EvaluationReportRepositoryPort
    tenant_repo: TenantAccountRepositoryPort
    consent_repo: ConsentRecordRepositoryPort


@dataclass(frozen=True, slots=True)
class ExternalAdapterRegistry:
    """Bundle of infrastructure outbound driven adapters injected at startup."""

    idempotency_store: IdempotencyStorePort
    distributed_lock: DistributedLockPort
    object_storage: ObjectStoragePort
    virus_scanner: VirusScannerPort
    pii_vault: PiiVaultPort
    task_dispatcher: TaskQueueDispatcherPort
    progress_publisher: RealtimeProgressPublisherPort
    observability_tracer: ObservabilityTracerPort
    document_parser: DocumentParserPort
    hybrid_vector_search: HybridVectorSearchPort
    adaptive_interview_graph: AdaptiveInterviewGraphPort
    audio_stt: AudioSttPort
    audio_tts: AudioTtsPort
    prompt_guardrail: PromptGuardrailPort
    token_budget_enforcer: TokenBudgetEnforcerPort


def build_in_memory_repositories() -> RepositoryRegistry:
    """Construct pure in-memory repository implementations for deterministic orchestration."""
    return RepositoryRegistry(
        requisition_repo=InMemoryJobRequisitionRepository(),
        batch_repo=InMemoryScreeningBatchRepository(),
        profile_repo=InMemoryCandidateProfileRepository(),
        match_repo=InMemorySemanticMatchResultRepository(),
        session_repo=InMemoryInterviewSessionRepository(),
        report_repo=InMemoryEvaluationReportRepository(),
        tenant_repo=InMemoryTenantAccountRepository(),
        consent_repo=InMemoryConsentRecordRepository(),
    )


def build_sql_repositories(db: SqlDatabaseManager) -> RepositoryRegistry:
    """Construct ACID SQL-backed repository implementations sharing ``db``."""
    return RepositoryRegistry(
        requisition_repo=SqlJobRequisitionRepository(db),
        batch_repo=SqlScreeningBatchRepository(db),
        profile_repo=SqlCandidateProfileRepository(db),
        match_repo=SqlSemanticMatchResultRepository(db),
        session_repo=SqlInterviewSessionRepository(db),
        report_repo=SqlEvaluationReportRepository(db),
        tenant_repo=SqlTenantAccountRepository(db),
        consent_repo=SqlConsentRecordRepository(db),
    )


@dataclass(frozen=True, slots=True)
class Phase3PipelineBundle:
    """Bundled DI container plus Phase 3 concrete adapters and DLQ executor."""

    container: ApplicationContainer
    adapters: ExternalAdapterRegistry
    dlq: InMemoryDeadLetterQueue
    resilient_executor: ResilientTaskExecutor


@dataclass(frozen=True, slots=True)
class Phase4PipelineBundle:
    """Bundled DI container plus Phase 4 stateful adaptive interview adapters and DLQ executor."""

    container: ApplicationContainer
    adapters: ExternalAdapterRegistry
    dlq: InMemoryDeadLetterQueue
    resilient_executor: ResilientTaskExecutor
    interview_graph: LangGraphAdaptiveInterviewAdapter
    guardrail: AdversarialPromptGuardrailAdapter
    token_budget_enforcer: SlidingWindowTokenBudgetEnforcerAdapter
    distributed_lock: FencingTokenDistributedLockAdapter


def build_phase4_external_adapters(
    *,
    llm_client: StructuredExtractionRunnableProtocol | None = None,
    embedding_client: DenseEmbeddingClientProtocol | None = None,
    vector_backend: VectorStoreBackendProtocol | None = None,
    cipher: EnvelopeCipherProtocol | None = None,
    master_kek: bytes | None = None,
    grounding_threshold: float = 0.85,
    checkpointer: InterviewStateCheckpointerProtocol | None = None,
    question_model: QuestionGenerationModelProtocol | None = None,
    evaluation_model: TurnEvaluationModelProtocol | None = None,
    socratic_model: SocraticProbeModelProtocol | None = None,
    synthesis_model: InterviewSynthesisModelProtocol | None = None,
    stt_driver: SttDriverProtocol | None = None,
    tts_driver: TtsDriverProtocol | None = None,
    max_tokens_per_window: int | None = None,
    idempotency_store: IdempotencyStorePort | None = None,
    pii_vault: PiiVaultPort | None = None,
    observability_tracer: ObservabilityTracerPort | None = None,
) -> ExternalAdapterRegistry:
    """Wire concrete Phase 3 & Phase 4 Document, Security, Vector, and LangGraph adapters."""
    object_storage = TenantScopedObjectStorageAdapter()
    virus_scanner = SignatureAndHeuristicVirusScannerAdapter()
    resolved_pii_vault: PiiVaultPort = (
        pii_vault
        if pii_vault is not None
        else CryptographicPiiVaultAdapter(cipher=cipher, master_kek=master_kek)
    )
    resolved_idempotency: IdempotencyStorePort = (
        idempotency_store if idempotency_store is not None else InMemoryIdempotencyStore()
    )
    tracer: ObservabilityTracerPort = (
        observability_tracer
        if observability_tracer is not None
        else InMemoryObservabilityTracer()
    )
    document_parser = LangChainDocumentParserAdapter(
        object_storage=object_storage,
        pii_vault=resolved_pii_vault,
        llm_client=llm_client,
        tracer=tracer,
        grounding_threshold=grounding_threshold,
    )
    resolved_backend: VectorStoreBackendProtocol = (
        vector_backend if vector_backend is not None else InMemoryHybridVectorStoreBackend()
    )
    hybrid_vector_search = HybridVectorSearchAdapter(
        backend=resolved_backend,
        embedding_client=embedding_client,
    )
    adaptive_interview_graph = LangGraphAdaptiveInterviewAdapter(
        checkpointer=checkpointer,
        question_model=question_model,
        evaluation_model=evaluation_model,
        socratic_model=socratic_model,
        synthesis_model=synthesis_model,
    )
    return ExternalAdapterRegistry(
        idempotency_store=resolved_idempotency,
        distributed_lock=FencingTokenDistributedLockAdapter(),
        object_storage=object_storage,
        virus_scanner=virus_scanner,
        pii_vault=resolved_pii_vault,
        task_dispatcher=InMemoryTaskQueueDispatcher(),
        progress_publisher=ReplayableSseProgressBroker(),
        observability_tracer=tracer,
        document_parser=document_parser,
        hybrid_vector_search=hybrid_vector_search,
        adaptive_interview_graph=adaptive_interview_graph,
        audio_stt=StreamingAudioSttAdapter(driver=stt_driver),
        audio_tts=StreamingAudioTtsAdapter(driver=tts_driver),
        prompt_guardrail=AdversarialPromptGuardrailAdapter(),
        token_budget_enforcer=SlidingWindowTokenBudgetEnforcerAdapter(
            max_tokens_per_window=max_tokens_per_window
        ),
    )


def build_phase3_external_adapters(
    *,
    llm_client: StructuredExtractionRunnableProtocol | None = None,
    embedding_client: DenseEmbeddingClientProtocol | None = None,
    vector_backend: VectorStoreBackendProtocol | None = None,
    cipher: EnvelopeCipherProtocol | None = None,
    master_kek: bytes | None = None,
    grounding_threshold: float = 0.85,
) -> ExternalAdapterRegistry:
    """Wire all concrete Phase 3 & Phase 4 adapters for backward-compatible container startup."""
    return build_phase4_external_adapters(
        llm_client=llm_client,
        embedding_client=embedding_client,
        vector_backend=vector_backend,
        cipher=cipher,
        master_kek=master_kek,
        grounding_threshold=grounding_threshold,
    )


def build_phase3_container(
    *,
    llm_client: StructuredExtractionRunnableProtocol | None = None,
    embedding_client: DenseEmbeddingClientProtocol | None = None,
    vector_backend: VectorStoreBackendProtocol | None = None,
    cipher: EnvelopeCipherProtocol | None = None,
    master_kek: bytes | None = None,
    grounding_threshold: float = 0.85,
) -> Phase3PipelineBundle:
    """Assemble a full ApplicationContainer wired with real Phase 3 adapters and DLQ resilience."""
    adapters = build_phase3_external_adapters(
        llm_client=llm_client,
        embedding_client=embedding_client,
        vector_backend=vector_backend,
        cipher=cipher,
        master_kek=master_kek,
        grounding_threshold=grounding_threshold,
    )
    container = ApplicationContainer(adapters=adapters)
    dlq = InMemoryDeadLetterQueue()
    executor = ResilientTaskExecutor(dlq=dlq)
    return Phase3PipelineBundle(
        container=container,
        adapters=adapters,
        dlq=dlq,
        resilient_executor=executor,
    )


def build_phase4_container(
    *,
    llm_client: StructuredExtractionRunnableProtocol | None = None,
    embedding_client: DenseEmbeddingClientProtocol | None = None,
    vector_backend: VectorStoreBackendProtocol | None = None,
    cipher: EnvelopeCipherProtocol | None = None,
    master_kek: bytes | None = None,
    grounding_threshold: float = 0.85,
    checkpointer: InterviewStateCheckpointerProtocol | None = None,
    question_model: QuestionGenerationModelProtocol | None = None,
    evaluation_model: TurnEvaluationModelProtocol | None = None,
    socratic_model: SocraticProbeModelProtocol | None = None,
    synthesis_model: InterviewSynthesisModelProtocol | None = None,
    stt_driver: SttDriverProtocol | None = None,
    tts_driver: TtsDriverProtocol | None = None,
    max_tokens_per_window: int | None = None,
) -> Phase4PipelineBundle:
    """Assemble a full ApplicationContainer wired with Phase 4 LangGraph interview adapters."""
    adapters = build_phase4_external_adapters(
        llm_client=llm_client,
        embedding_client=embedding_client,
        vector_backend=vector_backend,
        cipher=cipher,
        master_kek=master_kek,
        grounding_threshold=grounding_threshold,
        checkpointer=checkpointer,
        question_model=question_model,
        evaluation_model=evaluation_model,
        socratic_model=socratic_model,
        synthesis_model=synthesis_model,
        stt_driver=stt_driver,
        tts_driver=tts_driver,
        max_tokens_per_window=max_tokens_per_window,
    )
    container = ApplicationContainer(adapters=adapters)
    dlq = InMemoryDeadLetterQueue()
    executor = ResilientTaskExecutor(dlq=dlq)
    assert isinstance(adapters.adaptive_interview_graph, LangGraphAdaptiveInterviewAdapter)
    assert isinstance(adapters.prompt_guardrail, AdversarialPromptGuardrailAdapter)
    assert isinstance(adapters.token_budget_enforcer, SlidingWindowTokenBudgetEnforcerAdapter)
    assert isinstance(adapters.distributed_lock, FencingTokenDistributedLockAdapter)
    return Phase4PipelineBundle(
        container=container,
        adapters=adapters,
        dlq=dlq,
        resilient_executor=executor,
        interview_graph=adapters.adaptive_interview_graph,
        guardrail=adapters.prompt_guardrail,
        token_budget_enforcer=adapters.token_budget_enforcer,
        distributed_lock=adapters.distributed_lock,
    )


@dataclass(frozen=True, slots=True)
class Phase5PipelineBundle:
    """Bundled DI container plus Phase 5 REST/WS Gateway, Middleware, SSE & Priority Workers."""

    container: ApplicationContainer
    adapters: ExternalAdapterRegistry
    dlq: InMemoryDeadLetterQueue
    resilient_executor: ResilientTaskExecutor
    interview_graph: LangGraphAdaptiveInterviewAdapter
    guardrail: AdversarialPromptGuardrailAdapter
    token_budget_enforcer: SlidingWindowTokenBudgetEnforcerAdapter
    distributed_lock: FencingTokenDistributedLockAdapter
    sse_broker: ReplayableSseProgressBroker
    priority_limiter: PriorityReservedTokenBucketLimiter
    batch_coordinator: BatchIngestionWorkerCoordinator
    authenticator: BearerTokenAuthenticator
    rate_limiter: TenantRateLimiter
    rest_router: RestApiRouter
    ws_gateway: WebSocketInterviewGateway


@dataclass(frozen=True, slots=True)
class Phase6PipelineBundle:
    """Bundled DI container plus Phase 6 SQL Persistence, Outbox, Reconciler & Checkpointer."""

    db_manager: SqlDatabaseManager
    container: ApplicationContainer
    repositories: RepositoryRegistry
    adapters: ExternalAdapterRegistry
    uow: SqlTransactionalUnitOfWork
    event_bus: InMemoryDomainEventBus
    dlq: InMemoryDeadLetterQueue
    resilient_executor: ResilientTaskExecutor
    idempotency_store: SqlIdempotencyStoreAdapter
    pii_vault: SqlCandidatePiiVaultStore
    quantized_vector_backend: QuantizedHybridVectorStoreBackend
    outbox_relay: TransactionalOutboxRelayWorker
    reconciler: CrossStoreAntiEntropyReconciler
    checkpointer: TwoTierFencedStateCheckpointer
    interview_graph: LangGraphAdaptiveInterviewAdapter
    guardrail: AdversarialPromptGuardrailAdapter
    token_budget_enforcer: SlidingWindowTokenBudgetEnforcerAdapter
    distributed_lock: FencingTokenDistributedLockAdapter
    sse_broker: ReplayableSseProgressBroker
    priority_limiter: PriorityReservedTokenBucketLimiter
    batch_coordinator: BatchIngestionWorkerCoordinator
    authenticator: BearerTokenAuthenticator
    rate_limiter: TenantRateLimiter
    rest_router: RestApiRouter
    ws_gateway: WebSocketInterviewGateway


def _build_presentation_and_workers(
    *,
    container: ApplicationContainer,
    adapters: ExternalAdapterRegistry,
    executor: ResilientTaskExecutor,
    jwt_signing_key: bytes | None,
    rate_limit_default_per_minute: int,
    bucket_rate_limits: Mapping[str, int] | None,
    bucket_total_capacity: int,
    bucket_interview_reserve: int,
    bucket_matching_reserve: int,
    bucket_refill_tokens_per_second: float,
    clock_fn: Callable[[], float] | None,
    ops_controller: OpsController | None = None,
    ws_cluster_hooks: WebSocketClusterHooks | None = None,
) -> tuple[
    PriorityReservedTokenBucketLimiter,
    BatchIngestionWorkerCoordinator,
    BearerTokenAuthenticator,
    TenantRateLimiter,
    RestApiRouter,
    WebSocketInterviewGateway,
]:
    assert isinstance(adapters.progress_publisher, ReplayableSseProgressBroker)
    assert isinstance(adapters.task_dispatcher, InMemoryTaskQueueDispatcher)

    priority_limiter = PriorityReservedTokenBucketLimiter(
        total_capacity=bucket_total_capacity,
        interview_reserve=bucket_interview_reserve,
        matching_reserve=bucket_matching_reserve,
        refill_tokens_per_second=bucket_refill_tokens_per_second,
        clock_fn=clock_fn,
    )
    batch_coordinator = BatchIngestionWorkerCoordinator(
        task_dispatcher=adapters.task_dispatcher,
        process_task_use_case=container.process_document_task,
        get_batch_status_use_case=container.get_screening_batch_status,
        progress_publisher=adapters.progress_publisher,
        rate_limiter=priority_limiter,
        resilient_executor=executor,
    )

    authenticator = BearerTokenAuthenticator(signing_key=jwt_signing_key, clock_fn=clock_fn)
    rate_limiter = TenantRateLimiter(
        default_limit_per_minute=rate_limit_default_per_minute,
        bucket_limits=bucket_rate_limits,
        clock_fn=clock_fn,
    )
    error_translator = ExceptionToProblemDetailsTranslator()
    idempotency_mw = IdempotencyMiddleware(store=adapters.idempotency_store)

    rest_router = RestApiRouter(
        authenticator=authenticator,
        rate_limiter=rate_limiter,
        idempotency_middleware=idempotency_mw,
        recruitment_controller=RecruitmentController(
            create_requisition=container.create_job_requisition,
            publish_requisition=container.publish_job_requisition,
            update_rubric=container.update_job_rubric,
            archive_requisition=container.archive_job_requisition,
            get_requisition=container.get_job_requisition,
        ),
        document_controller=DocumentIntelligenceController(
            initiate_batch=container.initiate_screening_batch,
            get_batch_status=container.get_screening_batch_status,
            execute_matching=container.execute_hybrid_matching,
            progress_stream=adapters.progress_publisher,
        ),
        interview_controller=LiveInterviewController(
            initialize_interview=container.initialize_interview_session,
            record_telemetry=container.record_client_telemetry,
            terminate_interview=container.terminate_interview_early,
            complete_interview=container.complete_interview_session,
        ),
        evaluation_controller=EvaluationController(
            generate_report=container.generate_evaluation_report,
            record_override=container.record_recruiter_override,
            build_comparison_matrix=container.build_comparison_matrix,
        ),
        identity_controller=IdentityController(
            provision_tenant=container.provision_tenant_account,
            record_consent=container.record_candidate_consent,
            revoke_consent=container.revoke_candidate_consent,
            execute_erasure=container.execute_candidate_erasure,
        ),
        ops_controller=ops_controller,
        error_translator=error_translator,
    )
    ws_gateway = WebSocketInterviewGateway(
        authenticator=authenticator,
        lock_port=adapters.distributed_lock,
        process_turn_use_case=container.process_candidate_turn,
        record_telemetry_use_case=container.record_client_telemetry,
        terminate_interview_use_case=container.terminate_interview_early,
        stt_port=adapters.audio_stt,
        tts_port=adapters.audio_tts,
        error_translator=error_translator,
        cluster_hooks=ws_cluster_hooks,
    )
    return (
        priority_limiter,
        batch_coordinator,
        authenticator,
        rate_limiter,
        rest_router,
        ws_gateway,
    )


def build_phase5_container(
    *,
    llm_client: StructuredExtractionRunnableProtocol | None = None,
    embedding_client: DenseEmbeddingClientProtocol | None = None,
    vector_backend: VectorStoreBackendProtocol | None = None,
    cipher: EnvelopeCipherProtocol | None = None,
    master_kek: bytes | None = None,
    grounding_threshold: float = 0.85,
    checkpointer: InterviewStateCheckpointerProtocol | None = None,
    question_model: QuestionGenerationModelProtocol | None = None,
    evaluation_model: TurnEvaluationModelProtocol | None = None,
    socratic_model: SocraticProbeModelProtocol | None = None,
    synthesis_model: InterviewSynthesisModelProtocol | None = None,
    stt_driver: SttDriverProtocol | None = None,
    tts_driver: TtsDriverProtocol | None = None,
    max_tokens_per_window: int | None = None,
    jwt_signing_key: bytes | None = None,
    rate_limit_default_per_minute: int = 300,
    bucket_rate_limits: Mapping[str, int] | None = None,
    bucket_total_capacity: int = 100_000,
    bucket_interview_reserve: int = 20_000,
    bucket_matching_reserve: int = 15_000,
    bucket_refill_tokens_per_second: float = 5_000.0,
    clock_fn: Callable[[], float] | None = None,
) -> Phase5PipelineBundle:
    """Assemble full ApplicationContainer plus Phase 5 REST, SSE, WS, Middleware & Workers."""
    adapters = build_phase4_external_adapters(
        llm_client=llm_client,
        embedding_client=embedding_client,
        vector_backend=vector_backend,
        cipher=cipher,
        master_kek=master_kek,
        grounding_threshold=grounding_threshold,
        checkpointer=checkpointer,
        question_model=question_model,
        evaluation_model=evaluation_model,
        socratic_model=socratic_model,
        synthesis_model=synthesis_model,
        stt_driver=stt_driver,
        tts_driver=tts_driver,
        max_tokens_per_window=max_tokens_per_window,
    )
    container = ApplicationContainer(adapters=adapters)
    dlq = InMemoryDeadLetterQueue()
    executor = ResilientTaskExecutor(dlq=dlq)

    assert isinstance(adapters.adaptive_interview_graph, LangGraphAdaptiveInterviewAdapter)
    assert isinstance(adapters.prompt_guardrail, AdversarialPromptGuardrailAdapter)
    assert isinstance(adapters.token_budget_enforcer, SlidingWindowTokenBudgetEnforcerAdapter)
    assert isinstance(adapters.distributed_lock, FencingTokenDistributedLockAdapter)
    assert isinstance(adapters.progress_publisher, ReplayableSseProgressBroker)

    (
        priority_limiter,
        batch_coordinator,
        authenticator,
        rate_limiter,
        rest_router,
        ws_gateway,
    ) = _build_presentation_and_workers(
        container=container,
        adapters=adapters,
        executor=executor,
        jwt_signing_key=jwt_signing_key,
        rate_limit_default_per_minute=rate_limit_default_per_minute,
        bucket_rate_limits=bucket_rate_limits,
        bucket_total_capacity=bucket_total_capacity,
        bucket_interview_reserve=bucket_interview_reserve,
        bucket_matching_reserve=bucket_matching_reserve,
        bucket_refill_tokens_per_second=bucket_refill_tokens_per_second,
        clock_fn=clock_fn,
    )

    return Phase5PipelineBundle(
        container=container,
        adapters=adapters,
        dlq=dlq,
        resilient_executor=executor,
        interview_graph=adapters.adaptive_interview_graph,
        guardrail=adapters.prompt_guardrail,
        token_budget_enforcer=adapters.token_budget_enforcer,
        distributed_lock=adapters.distributed_lock,
        sse_broker=adapters.progress_publisher,
        priority_limiter=priority_limiter,
        batch_coordinator=batch_coordinator,
        authenticator=authenticator,
        rate_limiter=rate_limiter,
        rest_router=rest_router,
        ws_gateway=ws_gateway,
    )


def build_phase6_container(
    *,
    dsn: str = ":memory:",
    db_manager: SqlDatabaseManager | None = None,
    enable_wal: bool = True,
    l1_ttl_seconds: float = 300.0,
    dispatch_to_event_bus_on_commit: bool = True,
    relay_to_event_bus: bool = False,
    llm_client: StructuredExtractionRunnableProtocol | None = None,
    embedding_client: DenseEmbeddingClientProtocol | None = None,
    cipher: EnvelopeCipherProtocol | None = None,
    master_kek: bytes | None = None,
    grounding_threshold: float = 0.85,
    question_model: QuestionGenerationModelProtocol | None = None,
    evaluation_model: TurnEvaluationModelProtocol | None = None,
    socratic_model: SocraticProbeModelProtocol | None = None,
    synthesis_model: InterviewSynthesisModelProtocol | None = None,
    stt_driver: SttDriverProtocol | None = None,
    tts_driver: TtsDriverProtocol | None = None,
    max_tokens_per_window: int | None = None,
    jwt_signing_key: bytes | None = None,
    rate_limit_default_per_minute: int = 300,
    bucket_rate_limits: Mapping[str, int] | None = None,
    bucket_total_capacity: int = 100_000,
    bucket_interview_reserve: int = 20_000,
    bucket_matching_reserve: int = 15_000,
    bucket_refill_tokens_per_second: float = 5_000.0,
    clock_fn: Callable[[], float] | None = None,
) -> Phase6PipelineBundle:
    """Assemble full ACID Phase 6 SQL Persistence, Outbox, Quantized Vector & Fenced Stack."""
    db = (
        db_manager
        if db_manager is not None
        else SqlDatabaseManager(DatabaseConnectionConfig(dsn=dsn, enable_wal=enable_wal))
    )
    repositories = build_sql_repositories(db)
    event_bus = InMemoryDomainEventBus()
    uow = SqlTransactionalUnitOfWork(
        db=db,
        event_bus=event_bus,
        dispatch_to_event_bus_on_commit=dispatch_to_event_bus_on_commit,
    )
    idempotency_store = SqlIdempotencyStoreAdapter(db)
    pii_vault = SqlCandidatePiiVaultStore(db, cipher=cipher, master_kek=master_kek)
    quantized_backend = QuantizedHybridVectorStoreBackend(db=db)
    checkpointer = TwoTierFencedStateCheckpointer(
        db=db,
        l1_ttl_seconds=l1_ttl_seconds,
    )

    adapters = build_phase4_external_adapters(
        llm_client=llm_client,
        embedding_client=embedding_client,
        vector_backend=quantized_backend,
        cipher=cipher,
        master_kek=master_kek,
        grounding_threshold=grounding_threshold,
        checkpointer=checkpointer,
        question_model=question_model,
        evaluation_model=evaluation_model,
        socratic_model=socratic_model,
        synthesis_model=synthesis_model,
        stt_driver=stt_driver,
        tts_driver=tts_driver,
        max_tokens_per_window=max_tokens_per_window,
        idempotency_store=idempotency_store,
        pii_vault=pii_vault,
    )
    container = ApplicationContainer(
        adapters=adapters,
        repositories=repositories,
        event_bus=event_bus,
        uow=uow,
    )
    dlq = InMemoryDeadLetterQueue()
    executor = ResilientTaskExecutor(dlq=dlq)

    assert isinstance(repositories.profile_repo, SqlCandidateProfileRepository)
    assert isinstance(repositories.consent_repo, SqlConsentRecordRepository)

    outbox_relay = TransactionalOutboxRelayWorker(
        db=db,
        profile_repo=repositories.profile_repo,
        vector_search=adapters.hybrid_vector_search,
        vector_backend=quantized_backend,
        pii_vault=pii_vault,
        event_bus=event_bus,
        dlq=dlq,
        relay_to_event_bus=relay_to_event_bus,
    )
    reconciler = CrossStoreAntiEntropyReconciler(
        db=db,
        profile_repo=repositories.profile_repo,
        consent_repo=repositories.consent_repo,
        vector_backend=quantized_backend,
        vector_search=adapters.hybrid_vector_search,
        pii_vault=pii_vault,
    )

    assert isinstance(adapters.adaptive_interview_graph, LangGraphAdaptiveInterviewAdapter)
    assert isinstance(adapters.prompt_guardrail, AdversarialPromptGuardrailAdapter)
    assert isinstance(adapters.token_budget_enforcer, SlidingWindowTokenBudgetEnforcerAdapter)
    assert isinstance(adapters.distributed_lock, FencingTokenDistributedLockAdapter)
    assert isinstance(adapters.progress_publisher, ReplayableSseProgressBroker)

    (
        priority_limiter,
        batch_coordinator,
        authenticator,
        rate_limiter,
        rest_router,
        ws_gateway,
    ) = _build_presentation_and_workers(
        container=container,
        adapters=adapters,
        executor=executor,
        jwt_signing_key=jwt_signing_key,
        rate_limit_default_per_minute=rate_limit_default_per_minute,
        bucket_rate_limits=bucket_rate_limits,
        bucket_total_capacity=bucket_total_capacity,
        bucket_interview_reserve=bucket_interview_reserve,
        bucket_matching_reserve=bucket_matching_reserve,
        bucket_refill_tokens_per_second=bucket_refill_tokens_per_second,
        clock_fn=clock_fn,
    )

    return Phase6PipelineBundle(
        db_manager=db,
        container=container,
        repositories=repositories,
        adapters=adapters,
        uow=uow,
        event_bus=event_bus,
        dlq=dlq,
        resilient_executor=executor,
        idempotency_store=idempotency_store,
        pii_vault=pii_vault,
        quantized_vector_backend=quantized_backend,
        outbox_relay=outbox_relay,
        reconciler=reconciler,
        checkpointer=checkpointer,
        interview_graph=adapters.adaptive_interview_graph,
        guardrail=adapters.prompt_guardrail,
        token_budget_enforcer=adapters.token_budget_enforcer,
        distributed_lock=adapters.distributed_lock,
        sse_broker=adapters.progress_publisher,
        priority_limiter=priority_limiter,
        batch_coordinator=batch_coordinator,
        authenticator=authenticator,
        rate_limiter=rate_limiter,
        rest_router=rest_router,
        ws_gateway=ws_gateway,
    )


def build_ws_cluster_hooks(
    *,
    node_id: str,
    backplane: StatelessWebSocketPubSubBackplane,
    checkpointer: TwoTierFencedStateCheckpointer,
) -> WebSocketClusterHooks:
    """Bind WebSocketClusterHooks to shared Pub/Sub backplane and L2 SQL checkpointer."""

    def _subscribe(
        sub_node: str,
        tenant_id: TenantId,
        session_id: UUID,
        callback: Callable[[object], object],
    ) -> None:
        backplane.subscribe_session(
            node_id=sub_node,
            tenant_id=tenant_id,
            session_id=session_id,
            callback=callback,  # type: ignore[arg-type]
        )

    def _unsubscribe(sub_node: str, tenant_id: TenantId, session_id: UUID) -> None:
        backplane.unsubscribe_session(
            node_id=sub_node,
            tenant_id=tenant_id,
            session_id=session_id,
        )

    def _flush_checkpoint(tenant_id: TenantId, session_id: UUID) -> None:
        # Force L2 SQL verification so peer nodes reload an intact SHA-256 verified snapshot
        checkpointer.invalidate_l1(tenant_id=tenant_id, session_id=session_id)
        _ = checkpointer.load_latest(tenant_id, session_id)

    def _hydrate_session(
        tenant_id: TenantId, session_id: UUID
    ) -> Mapping[str, object] | None:
        snap = checkpointer.load_latest(tenant_id, session_id)
        if snap is None:
            return None
        rt = snap.state.routing
        return {
            "turn_number": rt.completed_turns + 1,
            "active_question": rt.active_prompt_text,
            "difficulty": int(rt.current_difficulty),
            "checkpoint_version": snap.checkpoint_version,
            "tokens_consumed": rt.total_tokens_consumed,
            "remaining_seconds": snap.state.metadata.max_wall_clock_seconds,
            "is_completed": rt.phase.value == "COMPLETED",
        }

    return WebSocketClusterHooks(
        node_id=node_id,
        subscribe_fn=_subscribe,
        unsubscribe_fn=_unsubscribe,
        publish_fn=lambda t_id, s_id, ev, pl, src, tr: backplane.publish_session_event(
            tenant_id=t_id,
            session_id=s_id,
            event_type=ev,
            payload=pl,
            source_node_id=src,
            trace_id=tr,
        ),
        mark_draining_fn=backplane.mark_draining,
        register_drain_handler_fn=lambda n_id, fn: backplane.register_node(
            n_id, drain_handler=fn
        ),
        checkpoint_flush_fn=_flush_checkpoint,
        session_hydrator_fn=_hydrate_session,
    )


@dataclass(frozen=True, slots=True)
class Phase8PipelineBundle:
    """Complete Production Stack (Phases 1-8): Persistence, Security, OTel, FinOps, Drift & Ops."""

    db_manager: SqlDatabaseManager
    container: ApplicationContainer
    repositories: RepositoryRegistry
    adapters: ExternalAdapterRegistry
    uow: SqlTransactionalUnitOfWork
    event_bus: InMemoryDomainEventBus
    dlq: InMemoryDeadLetterQueue
    resilient_executor: ResilientTaskExecutor
    idempotency_store: SqlIdempotencyStoreAdapter
    pii_vault: SqlCandidatePiiVaultStore
    quantized_vector_backend: QuantizedHybridVectorStoreBackend
    outbox_relay: TransactionalOutboxRelayWorker
    reconciler: CrossStoreAntiEntropyReconciler
    checkpointer: TwoTierFencedStateCheckpointer
    interview_graph: LangGraphAdaptiveInterviewAdapter
    guardrail: AdversarialPromptGuardrailAdapter
    defense_firewall: DefenseInDepthFirewall
    otel_tracer: StructuredOpenTelemetryTracerAdapter
    finops_controller: MultiTenantFinOpsController
    rubric_drift_monitor: RubricDriftMonitor
    token_budget_enforcer: SlidingWindowTokenBudgetEnforcerAdapter
    distributed_lock: FencingTokenDistributedLockAdapter
    sse_broker: ReplayableSseProgressBroker
    priority_limiter: PriorityReservedTokenBucketLimiter
    batch_coordinator: BatchIngestionWorkerCoordinator
    ws_backplane: StatelessWebSocketPubSubBackplane
    worker_autoscaler: QueueDepthWorkerAutoscaler
    authenticator: BearerTokenAuthenticator
    rate_limiter: TenantRateLimiter
    ops_controller: OpsController
    rest_router: RestApiRouter
    ws_gateway: WebSocketInterviewGateway
    live_llm_client: OpenAiCompatibleJsonLlmClient | None = None

    def llm_runtime_metadata(self) -> tuple[str, str, str]:
        """Return (mode, provider, model) for runtime observability and demo manifests."""
        if self.live_llm_client is not None and self.live_llm_client.config.is_live_enabled:
            cfg = self.live_llm_client.config
            return ("live", cfg.provider, cfg.model)
        return ("deterministic", "deterministic", "deterministic-offline-v1")

    def create_asgi_app(
        self,
        *,
        cors_allowed_origins: tuple[str, ...] = ("*",),
    ) -> AsgiAppFn:
        """Assemble the standards-compliant ASGI 3.0 HTTP/SSE/OpenAPI/WS application."""
        return create_asgi_app(
            rest_router=self.rest_router,
            ws_gateway=self.ws_gateway,
            cors_allowed_origins=cors_allowed_origins,
        )

    def create_peer_ws_gateway(self, *, node_id: str) -> WebSocketInterviewGateway:
        """Instantiate a peer WebSocket gateway sharing DB, lock, checkpointer & backplane."""
        hooks = build_ws_cluster_hooks(
            node_id=node_id,
            backplane=self.ws_backplane,
            checkpointer=self.checkpointer,
        )
        return WebSocketInterviewGateway(
            authenticator=self.authenticator,
            lock_port=self.distributed_lock,
            process_turn_use_case=self.container.process_candidate_turn,
            record_telemetry_use_case=self.container.record_client_telemetry,
            terminate_interview_use_case=self.container.terminate_interview_early,
            stt_port=self.adapters.audio_stt,
            tts_port=self.adapters.audio_tts,
            cluster_hooks=hooks,
        )


def build_phase8_container(
    *,
    dsn: str = ":memory:",
    db_manager: SqlDatabaseManager | None = None,
    enable_wal: bool = True,
    l1_ttl_seconds: float = 300.0,
    dispatch_to_event_bus_on_commit: bool = True,
    relay_to_event_bus: bool = False,
    ws_node_id: str = "ws-node-1",
    session_token_quota: int = 45_000,
    default_tenant_token_quota: int = 2_000_000,
    kappa_alert_threshold: float = 0.70,
    llm_client: StructuredExtractionRunnableProtocol | None = None,
    embedding_client: DenseEmbeddingClientProtocol | None = None,
    cipher: EnvelopeCipherProtocol | None = None,
    master_kek: bytes | None = None,
    grounding_threshold: float = 0.85,
    question_model: QuestionGenerationModelProtocol | None = None,
    evaluation_model: TurnEvaluationModelProtocol | None = None,
    socratic_model: SocraticProbeModelProtocol | None = None,
    synthesis_model: InterviewSynthesisModelProtocol | None = None,
    stt_driver: SttDriverProtocol | None = None,
    tts_driver: TtsDriverProtocol | None = None,
    max_tokens_per_window: int | None = None,
    jwt_signing_key: bytes | None = None,
    rate_limit_default_per_minute: int = 300,
    bucket_rate_limits: Mapping[str, int] | None = None,
    bucket_total_capacity: int = 100_000,
    bucket_interview_reserve: int = 20_000,
    bucket_matching_reserve: int = 15_000,
    bucket_refill_tokens_per_second: float = 5_000.0,
    clock_fn: Callable[[], float] | None = None,
    demo_mode_enabled: bool = True,
    live_llm_client: OpenAiCompatibleJsonLlmClient | None = None,
) -> Phase8PipelineBundle:
    """Assemble full Phase 1-8 stack: Security, OTel, FinOps, Drift, WS Backplane & SQL."""
    db = (
        db_manager
        if db_manager is not None
        else SqlDatabaseManager(DatabaseConnectionConfig(dsn=dsn, enable_wal=enable_wal))
    )
    repositories = build_sql_repositories(db)
    event_bus = InMemoryDomainEventBus()
    uow = SqlTransactionalUnitOfWork(
        db=db,
        event_bus=event_bus,
        dispatch_to_event_bus_on_commit=dispatch_to_event_bus_on_commit,
    )
    idempotency_store = SqlIdempotencyStoreAdapter(db)
    pii_vault = SqlCandidatePiiVaultStore(db, cipher=cipher, master_kek=master_kek)
    quantized_backend = QuantizedHybridVectorStoreBackend(db=db)
    checkpointer = TwoTierFencedStateCheckpointer(
        db=db,
        l1_ttl_seconds=l1_ttl_seconds,
    )
    otel_tracer = StructuredOpenTelemetryTracerAdapter(clock_fn=clock_fn)
    finops_controller = MultiTenantFinOpsController(
        session_token_quota=session_token_quota,
        default_tenant_token_quota=default_tenant_token_quota,
    )
    rubric_drift_monitor = RubricDriftMonitor(kappa_alert_threshold=kappa_alert_threshold)
    ws_backplane = StatelessWebSocketPubSubBackplane()
    worker_autoscaler = QueueDepthWorkerAutoscaler(clock_fn=clock_fn)
    worker_autoscaler.register_pool(
        WorkerPoolScalingPolicy(pool_name="pool_parse_pii", min_replicas=1, max_replicas=20)
    )
    worker_autoscaler.register_pool(
        WorkerPoolScalingPolicy(
            pool_name="pool_llm_extract",
            min_replicas=1,
            max_replicas=25,
            concurrency_per_pod=10,
        )
    )
    worker_autoscaler.register_pool(
        WorkerPoolScalingPolicy(pool_name="pool_hybrid_match", min_replicas=1, max_replicas=16)
    )

    adapters = build_phase4_external_adapters(
        llm_client=llm_client,
        embedding_client=embedding_client,
        vector_backend=quantized_backend,
        cipher=cipher,
        master_kek=master_kek,
        grounding_threshold=grounding_threshold,
        checkpointer=checkpointer,
        question_model=question_model,
        evaluation_model=evaluation_model,
        socratic_model=socratic_model,
        synthesis_model=synthesis_model,
        stt_driver=stt_driver,
        tts_driver=tts_driver,
        max_tokens_per_window=max_tokens_per_window,
        idempotency_store=idempotency_store,
        pii_vault=pii_vault,
        observability_tracer=otel_tracer,
    )
    container = ApplicationContainer(
        adapters=adapters,
        repositories=repositories,
        event_bus=event_bus,
        uow=uow,
    )
    dlq = InMemoryDeadLetterQueue()
    executor = ResilientTaskExecutor(dlq=dlq)

    assert isinstance(repositories.profile_repo, SqlCandidateProfileRepository)
    assert isinstance(repositories.consent_repo, SqlConsentRecordRepository)

    outbox_relay = TransactionalOutboxRelayWorker(
        db=db,
        profile_repo=repositories.profile_repo,
        vector_search=adapters.hybrid_vector_search,
        vector_backend=quantized_backend,
        pii_vault=pii_vault,
        event_bus=event_bus,
        dlq=dlq,
        relay_to_event_bus=relay_to_event_bus,
    )
    reconciler = CrossStoreAntiEntropyReconciler(
        db=db,
        profile_repo=repositories.profile_repo,
        consent_repo=repositories.consent_repo,
        vector_backend=quantized_backend,
        vector_search=adapters.hybrid_vector_search,
        pii_vault=pii_vault,
    )

    assert isinstance(adapters.adaptive_interview_graph, LangGraphAdaptiveInterviewAdapter)
    assert isinstance(adapters.prompt_guardrail, AdversarialPromptGuardrailAdapter)
    assert isinstance(adapters.token_budget_enforcer, SlidingWindowTokenBudgetEnforcerAdapter)
    assert isinstance(adapters.distributed_lock, FencingTokenDistributedLockAdapter)
    assert isinstance(adapters.progress_publisher, ReplayableSseProgressBroker)

    defense_firewall = DefenseInDepthFirewall(guardrail=adapters.prompt_guardrail)
    ws_cluster_hooks = build_ws_cluster_hooks(
        node_id=ws_node_id,
        backplane=ws_backplane,
        checkpointer=checkpointer,
    )

    def _readiness_probe() -> tuple[bool, Mapping[str, object]]:
        sql_ok = False
        vector_ok = False
        ckpt_ok = False
        try:
            sql_ok = db.fetchone("SELECT 1 AS ok") is not None
        except Exception:
            sql_ok = False
        try:
            vector_ok = (
                db.fetchone("SELECT COUNT(*) AS cnt FROM vector_sync_watermarks") is not None
            )
        except Exception:
            vector_ok = False
        try:
            ckpt_ok = (
                db.fetchone("SELECT COUNT(*) AS cnt FROM interview_state_checkpoints") is not None
            )
        except Exception:
            ckpt_ok = False
        draining = ws_backplane.is_node_draining(ws_node_id)
        checks: dict[str, object] = {
            "sql_database": "ok" if sql_ok else "unavailable",
            "vector_store": "ok" if vector_ok else "unavailable",
            "checkpointer": "ok" if ckpt_ok else "unavailable",
            "draining": draining,
        }
        return (sql_ok and vector_ok and ckpt_ok and not draining), checks

    def _metrics_provider() -> Mapping[str, object]:
        llm_stats = (
            live_llm_client.telemetry_counts
            if live_llm_client is not None
            else {
                "provider": "deterministic",
                "model": "deterministic-offline-v1",
                "live_mode": "deterministic",
                "live_calls": 0,
                "fallback_calls": 0,
            }
        )
        return {
            "latency_percentiles": otel_tracer.summarize_operations(),
            "finops_ledger": finops_controller.summarize_ledger(),
            "rubric_drift": rubric_drift_monitor.summarize(),
            "autoscaler": worker_autoscaler.summarize(),
            "gateway_backplane": ws_backplane.summarize(),
            "llm_runtime": llm_stats,
        }

    bundle_holder: list[Phase8PipelineBundle] = []

    async def _demo_manifest_provider() -> Mapping[str, object]:
        from talent_screener.composition_root.demo_seeder import (
            seed_interactive_demo_environment,
        )

        manifest = await seed_interactive_demo_environment(bundle_holder[0])
        return manifest.to_dict()

    async def _demo_simulate_batch() -> Mapping[str, object]:
        from talent_screener.composition_root.demo_seeder import (
            simulate_live_demo_batch,
        )

        return await simulate_live_demo_batch(bundle_holder[0])

    ops_controller = OpsController(
        node_id=ws_node_id,
        readiness_probe_fn=_readiness_probe,
        metrics_provider_fn=_metrics_provider,
        demo_manifest_provider_fn=_demo_manifest_provider,
        demo_simulate_batch_fn=_demo_simulate_batch,
        demo_mode_enabled=demo_mode_enabled,
    )

    (
        priority_limiter,
        batch_coordinator,
        authenticator,
        rate_limiter,
        rest_router,
        ws_gateway,
    ) = _build_presentation_and_workers(
        container=container,
        adapters=adapters,
        executor=executor,
        jwt_signing_key=jwt_signing_key,
        rate_limit_default_per_minute=rate_limit_default_per_minute,
        bucket_rate_limits=bucket_rate_limits,
        bucket_total_capacity=bucket_total_capacity,
        bucket_interview_reserve=bucket_interview_reserve,
        bucket_matching_reserve=bucket_matching_reserve,
        bucket_refill_tokens_per_second=bucket_refill_tokens_per_second,
        clock_fn=clock_fn,
        ops_controller=ops_controller,
        ws_cluster_hooks=ws_cluster_hooks,
    )

    bundle = Phase8PipelineBundle(
        db_manager=db,
        container=container,
        repositories=repositories,
        adapters=adapters,
        uow=uow,
        event_bus=event_bus,
        dlq=dlq,
        resilient_executor=executor,
        idempotency_store=idempotency_store,
        pii_vault=pii_vault,
        quantized_vector_backend=quantized_backend,
        outbox_relay=outbox_relay,
        reconciler=reconciler,
        checkpointer=checkpointer,
        interview_graph=adapters.adaptive_interview_graph,
        guardrail=adapters.prompt_guardrail,
        defense_firewall=defense_firewall,
        otel_tracer=otel_tracer,
        finops_controller=finops_controller,
        rubric_drift_monitor=rubric_drift_monitor,
        token_budget_enforcer=adapters.token_budget_enforcer,
        distributed_lock=adapters.distributed_lock,
        sse_broker=adapters.progress_publisher,
        priority_limiter=priority_limiter,
        batch_coordinator=batch_coordinator,
        ws_backplane=ws_backplane,
        worker_autoscaler=worker_autoscaler,
        authenticator=authenticator,
        rate_limiter=rate_limiter,
        ops_controller=ops_controller,
        rest_router=rest_router,
        ws_gateway=ws_gateway,
        live_llm_client=live_llm_client,
    )
    bundle_holder.append(bundle)
    return bundle


build_production_container = build_phase8_container


def build_runtime_container_from_env(
    environ: Mapping[str, str] | None = None,
) -> Phase8PipelineBundle:
    """Assemble full-stack runtime container from environment variables (.env friendly)."""
    env = environ if environ is not None else os.environ
    dsn = env.get("DB_DSN", ":memory:").strip() or ":memory:"
    ws_node_id = env.get("WS_NODE_ID", "ws-node-1").strip() or "ws-node-1"
    raw_secret = env.get("JWT_SECRET", "").strip()
    jwt_key = raw_secret.encode("utf-8") if len(raw_secret.encode("utf-8")) >= 16 else None
    demo_raw = env.get("DEMO_SEED_ENABLED", "true").strip().lower()
    demo_mode_enabled = demo_raw not in {"0", "false", "no", "off"}

    live_client = build_live_llm_client_from_env(env)
    return build_phase8_container(
        dsn=dsn,
        ws_node_id=ws_node_id,
        jwt_signing_key=jwt_key,
        demo_mode_enabled=demo_mode_enabled,
        live_llm_client=live_client,
        llm_client=live_client,
        question_model=live_client,
        evaluation_model=live_client,
        socratic_model=live_client,
        synthesis_model=live_client,
    )


class ApplicationContainer:
    """Framework-agnostic Composition Root assembling Use Cases and Event Choreography."""

    def __init__(
        self,
        *,
        adapters: ExternalAdapterRegistry,
        repositories: RepositoryRegistry | None = None,
        event_bus: DomainEventBusPort | None = None,
        uow: TransactionalUnitOfWorkPort | None = None,
    ) -> None:
        self._adapters = adapters
        self._repos = repositories if repositories is not None else build_in_memory_repositories()
        self._event_bus: DomainEventBusPort = (
            event_bus if event_bus is not None else InMemoryDomainEventBus()
        )
        self._uow: TransactionalUnitOfWorkPort = (
            uow if uow is not None else InMemoryUnitOfWork(event_bus=self._event_bus)
        )

        # Recruitment Management Use Cases
        self.create_job_requisition = CreateJobRequisitionUseCase(
            requisition_repo=self._repos.requisition_repo,
            uow=self._uow,
        )
        self.publish_job_requisition = PublishJobRequisitionUseCase(
            requisition_repo=self._repos.requisition_repo,
            uow=self._uow,
        )
        self.update_job_rubric = UpdateJobRubricUseCase(
            requisition_repo=self._repos.requisition_repo,
            uow=self._uow,
        )
        self.lock_job_rubric = LockJobRequisitionRubricUseCase(
            requisition_repo=self._repos.requisition_repo,
            uow=self._uow,
        )
        self.archive_job_requisition = ArchiveJobRequisitionUseCase(
            requisition_repo=self._repos.requisition_repo,
            uow=self._uow,
        )
        self.get_job_requisition = GetJobRequisitionUseCase(
            requisition_repo=self._repos.requisition_repo,
        )

        # Document Intelligence & Matching Use Cases
        self.initiate_screening_batch = InitiateScreeningBatchUseCase(
            tenant_repo=self._repos.tenant_repo,
            requisition_repo=self._repos.requisition_repo,
            batch_repo=self._repos.batch_repo,
            idempotency_store=self._adapters.idempotency_store,
            object_storage=self._adapters.object_storage,
            task_dispatcher=self._adapters.task_dispatcher,
            uow=self._uow,
        )
        self.process_document_task = ProcessDocumentIngestionTaskUseCase(
            batch_repo=self._repos.batch_repo,
            profile_repo=self._repos.profile_repo,
            object_storage=self._adapters.object_storage,
            virus_scanner=self._adapters.virus_scanner,
            document_parser=self._adapters.document_parser,
            pii_vault=self._adapters.pii_vault,
            vector_search=self._adapters.hybrid_vector_search,
            progress_publisher=self._adapters.progress_publisher,
            tracer=self._adapters.observability_tracer,
            uow=self._uow,
        )
        self.execute_hybrid_matching = ExecuteHybridMatchingUseCase(
            requisition_repo=self._repos.requisition_repo,
            profile_repo=self._repos.profile_repo,
            match_repo=self._repos.match_repo,
            vector_search=self._adapters.hybrid_vector_search,
            uow=self._uow,
        )
        self.get_screening_batch_status = GetScreeningBatchStatusUseCase(
            batch_repo=self._repos.batch_repo,
        )

        # Live Adaptive Interview Use Cases
        self.initialize_interview_session = InitializeInterviewSessionUseCase(
            tenant_repo=self._repos.tenant_repo,
            consent_repo=self._repos.consent_repo,
            requisition_repo=self._repos.requisition_repo,
            profile_repo=self._repos.profile_repo,
            match_repo=self._repos.match_repo,
            session_repo=self._repos.session_repo,
            interview_graph=self._adapters.adaptive_interview_graph,
            uow=self._uow,
        )
        self.process_candidate_turn = ProcessCandidateTurnUseCase(
            session_repo=self._repos.session_repo,
            requisition_repo=self._repos.requisition_repo,
            profile_repo=self._repos.profile_repo,
            lock_port=self._adapters.distributed_lock,
            guardrail_port=self._adapters.prompt_guardrail,
            token_budget_enforcer=self._adapters.token_budget_enforcer,
            interview_graph=self._adapters.adaptive_interview_graph,
            stt_port=self._adapters.audio_stt,
            tts_port=self._adapters.audio_tts,
            tracer=self._adapters.observability_tracer,
            uow=self._uow,
        )
        self.record_client_telemetry = RecordClientTelemetryUseCase(
            session_repo=self._repos.session_repo,
            uow=self._uow,
        )
        self.terminate_interview_early = TerminateInterviewEarlyUseCase(
            session_repo=self._repos.session_repo,
            uow=self._uow,
        )
        self.complete_interview_session = CompleteInterviewSessionUseCase(
            session_repo=self._repos.session_repo,
            interview_graph=self._adapters.adaptive_interview_graph,
            uow=self._uow,
        )

        # Evaluation & Analytics Use Cases
        self.generate_evaluation_report = GenerateEvaluationReportUseCase(
            session_repo=self._repos.session_repo,
            requisition_repo=self._repos.requisition_repo,
            match_repo=self._repos.match_repo,
            report_repo=self._repos.report_repo,
            uow=self._uow,
        )
        self.record_recruiter_override = RecordRecruiterOverrideUseCase(
            report_repo=self._repos.report_repo,
            uow=self._uow,
        )
        self.build_comparison_matrix = BuildCandidateComparisonMatrixUseCase(
            requisition_repo=self._repos.requisition_repo,
            report_repo=self._repos.report_repo,
            match_repo=self._repos.match_repo,
        )

        # Identity & Governance Use Cases
        self.provision_tenant_account = ProvisionTenantAccountUseCase(
            tenant_repo=self._repos.tenant_repo,
            uow=self._uow,
        )
        self.record_candidate_consent = RecordCandidateConsentUseCase(
            tenant_repo=self._repos.tenant_repo,
            consent_repo=self._repos.consent_repo,
            uow=self._uow,
        )
        self.revoke_candidate_consent = RevokeCandidateConsentUseCase(
            consent_repo=self._repos.consent_repo,
            uow=self._uow,
        )
        self.execute_candidate_erasure = ExecuteCandidateErasureUseCase(
            consent_repo=self._repos.consent_repo,
            pii_vault=self._adapters.pii_vault,
            vector_search=self._adapters.hybrid_vector_search,
            uow=self._uow,
        )

        self.wire_event_handlers(self._event_bus)

    @property
    def unit_of_work(self) -> TransactionalUnitOfWorkPort:
        return self._uow

    @property
    def event_bus(self) -> DomainEventBusPort:
        return self._event_bus

    @property
    def repositories(self) -> RepositoryRegistry:
        return self._repos

    @property
    def adapters(self) -> ExternalAdapterRegistry:
        return self._adapters

    def wire_event_handlers(self, event_bus: DomainEventBusPort) -> None:
        """Bind cross-context Domain Event Handlers onto the event bus."""
        lock_handler = LockRubricOnBatchOrInterviewStartedHandler(
            lock_use_case=self.lock_job_rubric
        )
        event_bus.subscribe(ScreeningBatchCreatedEvent, lock_handler)
        event_bus.subscribe(InterviewSessionStartedEvent, lock_handler)

        report_handler = TriggerEvaluationReportOnInterviewCompletedHandler(
            generate_report_use_case=self.generate_evaluation_report
        )
        event_bus.subscribe(InterviewSessionCompletedEvent, report_handler)
