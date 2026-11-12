"""Composition Root Dependency Injection container wiring Ports to Adapters (ADR-002)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

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
    ReplayableSseProgressBroker,
    ResilientTaskExecutor,
)
from talent_screener.infrastructure.observability import InMemoryObservabilityTracer
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
    RecruitmentController,
    RestApiRouter,
)
from talent_screener.presentation.websocket import WebSocketInterviewGateway


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
    tracer = InMemoryObservabilityTracer()
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
