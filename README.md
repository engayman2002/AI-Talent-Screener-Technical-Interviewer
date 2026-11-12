# AI Talent Screener & Technical Interviewer

![Phase 1](https://img.shields.io/badge/Phase%201-Complete-brightgreen)
![Phase 2](https://img.shields.io/badge/Phase%202-Complete-brightgreen)
![Phase 3](https://img.shields.io/badge/Phase%203-Complete-brightgreen)
![Phase 4](https://img.shields.io/badge/Phase%204-Complete-brightgreen)
![Phase 5](https://img.shields.io/badge/Phase%205-Complete-brightgreen)
![Phase 6](https://img.shields.io/badge/Phase%206-Complete-brightgreen)
![Phase 7](https://img.shields.io/badge/Phase%207-Complete-brightgreen)
![Phase 8](https://img.shields.io/badge/Phase%208-Complete-brightgreen)
![Architecture](https://img.shields.io/badge/Architecture-Clean%20Architecture%20%2B%20DDD-blue)
![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB)
![TypeScript](https://img.shields.io/badge/TypeScript-strict-3178C6)
![Typing](https://img.shields.io/badge/mypy--strict-passing-success)

An enterprise **AI Talent Screener & Stateful Adaptive Technical Interviewer** engineered around **Clean Architecture**, **Hexagonal Ports & Adapters**, and **Strategic & Tactical Domain-Driven Design (DDD)**.

---

## Executive Architectural Overview

Manual high-volume technical screening breaks down under heterogeneous resume formats, keyword-based ATS false negatives, resume exaggeration, and hundreds of repetitive L1 phone screens. This platform separates deterministic business policies, mathematical scoring, and cognitive state machines from volatile AI frameworks (`LangChain`, `LangGraph`, LLM vendors, vector stores) across strict concentric boundaries:

1. **Domain Layer (`backend/src/talent_screener/domain/`):** 100% pure Python Standard Library core owning Aggregate Roots, Entities, immutable Value Objects, Domain Services, Domain Events, and Repository Protocols across all **5 Bounded Contexts**.
2. **Application Layer (`backend/src/talent_screener/application/`):** 100% pure Python Standard Library orchestration layer containing **22 Single-Responsibility Use Case Interactors**, segregated Inbound & Outbound `Protocol` Ports, immutable `@dataclass(frozen=True, slots=True)` Request/Response DTO boundaries, and cross-context Domain Event choreography handlers.
3. **Phase 3 Document Intelligence & Hybrid Vector Infrastructure (`infrastructure/`, `composition_root/`):** Concrete Document Intelligence pipeline adapters wired via `build_phase3_container()` — malware/magic-byte virus scanner, zero-trust cryptographic PII vault (`ADR-008` with $O(1)$ GDPR crypto-shredding), tenant-scoped object storage, full-jitter retry & DLQ resilience, layout-aware PDF/DOCX/Text normalizer, anti-hallucination grounding verifier, LangChain structured extraction ACL, `ADR-004` Parent-Child hierarchical chunker, BM25 + dense vector encoders, and multi-tenant `HybridVectorSearchAdapter`.
4. **Phase 4 Stateful Adaptive Interview Engine & Security Guardrails (`infrastructure/ai_adapters/langgraph_interview/`, `composition_root/`):** Production `ADR-005` 4-Plane LangGraph execution engine wired via `build_phase4_container()` — tenant-isolated `ADR-009` fenced state checkpointer, XML-delimited cognitive nodes (`QuestionGeneratorNode`, `EvaluationNode`, `SocraticFollowUpNode`, `ScoringVerificationNode`), piecewise P1–P6 `AdaptiveEdgeRouter`, `ADR-012` adversarial prompt-injection firewall, `ADR-013` sliding-window FinOps token budget enforcer, monotonic fencing distributed lock, and streaming STT/TTS audio ACLs.
5. **Phase 5 Presentation API, RFC 9457 Governance, Priority Batch Orchestration & WebSocket Protocol (`presentation/`, `infrastructure/messaging/`):** Clean-Architecture delivery layer wired via `build_phase5_container()` featuring a 20-endpoint v1 REST API router, RFC 9457 `ProblemDetailsResponse` translator (`400`–`503`), HS256 Bearer JWT authentication & RBAC/cross-tenant middleware, SHA-256 `Idempotency-Key` replay, per-tenant HTTP rate limits, `ADR-006` `PriorityReservedTokenBucketLimiter` + `BatchIngestionWorkerCoordinator` (WFQ + Little's Law bounds), replayable `Last-Event-ID` SSE progress streams, and `ADR-007` full-duplex WebSocket gateway (`/v1/interviews/{session_id}/ws`) with adaptive silence thresholds ($T_{\text{silence}}$) and preemptive TTS barge-in cancellation.
6. **Phase 6 ACID Relational Persistence, Transactional Outbox, INT8 Quantization & Fenced Checkpointing (`infrastructure/persistence/`):** Production persistence stack wired via `build_phase6_container()` — dual SQLite WAL/PostgreSQL DDL schema (`SqlDatabaseManager`, 17 relational tables), bidirectional Domain Data Mappers with strict `tenant_id` isolation and Optimistic Concurrency Control (`version = version + 1`), `SqlTransactionalUnitOfWork` atomically writing `transactional_outbox` rows, `SqlIdempotencyStoreAdapter`, `SqlCandidatePiiVaultStore` (`ADR-008`), `Int8ScalarQuantizer` + `QuantizedHybridVectorStoreBackend` (oversampled INT8 pre-filter + exact `float32` cosine rescoring), `TransactionalOutboxRelayWorker` + `CrossStoreAntiEntropyReconciler`, and `ADR-009` `TwoTierFencedStateCheckpointer` (TTL L1 cache + SHA-256-verified SQL L2 with monotonic fencing).
7. **Phase 7 Frontend Clean Architecture & Next.js App Router Portal (`frontend/`):** Four-layer strict TypeScript (`strict`, `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`, zero `any`) frontend engineered around `ADR-010` and `ADR-011` — pure Domain 13-state Interview FSM, Web Audio ring-buffer & VAD barge-in math, non-intrusive keystroke/paste/focus telemetry math, Empirical Bayes candidate comparison algebra, framework-agnostic Application Coordinators & Outbound Ports, Infrastructure WebSocket/rAF-coalesced SSE/Web Audio/DOM/REST adapters, manual DI `createFrontendServices()` composition root, and Next.js 15 App Router UI portals.
8. **Phase 8 AI Security Defense-in-Depth, OpenTelemetry Observability, FinOps Governance, Rubric Drift & Production Topology (`infrastructure/security/`, `infrastructure/observability/`, `infrastructure/messaging/`, `presentation/rest/`, `docker-compose.production.yml`):** Complete production stack wired via `build_phase8_container()` (`build_production_container`) — cryptographic nonce delimiter sandbox (`NonceDelimiterEnvelope`), session canary leak quarantine (`CanaryTokenLeakVerifier`), Dual-LLM Reader->Judge extraction (`DualLlmQuarantineExtractor`), escalating Three-Strike policy (`AdversarialStrikeTracker`), PII-scrubbing W3C `traceparent` OTel tracer (`StructuredOpenTelemetryTracerAdapter`), 80%/100% FinOps model tier router (`MultiTenantFinOpsController`), PSI / two-sample $D_{\text{KS}}$ / Cohen's $\kappa$ calibration monitor (`RubricDriftMonitor`), cross-node WebSocket Pub/Sub backplane with graceful node draining (`StatelessWebSocketPubSubBackplane`), hysteresis worker autoscaler (`QueueDepthWorkerAutoscaler`), operational health/readiness/metrics endpoints (`/health/live`, `/health/ready`, `/v1/ops/metrics`), multi-stage Dockerfiles, `docker-compose.production.yml`, and `.github/workflows/ci.yml`.

### Core Bounded Contexts

| Bounded Context | Package Path | Key Tactical Building Blocks |
| :--- | :--- | :--- |
| **Shared Kernel** | `domain/shared/` | `Entity[TId]`, `AggregateRoot[TId]`, `ValueObject`, `DomainEvent`, `DomainError`, `TenantId`, `SHA256Hash`, `VersionNumber`, `ConfidenceScore`, `NormalizedScore`, monotonic RFC 9562 `generate_uuid7` |
| **Recruitment Management** | `domain/contexts/recruitment/` | `JobRequisition` (rubric locking & lifecycle state machine), `CompetencyCriterion`, `CompetencyWeightVector` ($\sum w_i = 1.0 \pm 10^{-6}$), `HardSkillGatekeeperSet`, `TierThresholdConfig` |
| **Document Intelligence & Matching** | `domain/contexts/document_intelligence/` | `ScreeningBatch` (500-file quota, SHA-256 dedup, atomic counters), `CandidateProfile` (verbatim `source_quote` grounding, non-overlapping tenure union), `SemanticMatchResult`, `HybridMatchVector`, `RecencyDecayFactor`, `HybridMatchingDomainService` |
| **Live Adaptive Interview** | `domain/contexts/live_interview/` | `InterviewSession` (monotonic turns, $N_{\text{probe}} \le 2$ Socratic cap, token budgets), `AdaptiveTurn`, `SocraticProbe`, `TurnEvaluationScore`, `AdaptiveRoutingDomainService` |
| **Evaluation & Analytics** | `domain/contexts/evaluation_analytics/` | `EvaluationReport` (evidence turn citations, SHA-256 finalization seal, append-only `RecruiterOverrideAudit`), `AuthenticityIndex`, `CompositeRubricScore` |
| **Identity & Tenant Governance** | `domain/contexts/identity_governance/` | `TenantAccount`, `ConsentRecord` (GDPR Art. 17 erasure & crypto-shredding lifecycle), `QuotaPolicy`, `RetentionPolicy` |

### Phase 2 Application Orchestration & Clean Architecture Governance

| Module | Package Path | Implemented Use Cases, Handlers & Contracts |
| :--- | :--- | :--- |
| **Common Primitives** | `application/common/` | `ApplicationError` hierarchy (`ResourceNotFoundError`, `ResourceConflictError`, `IdempotencyConflictError`, `UnauthorizedTenantAccessError`, `QuotaExceededError`, `ExternalAdapterFailureError`, `GuardrailViolationError`), `UseCasePort`, `QueryUseCasePort`, `TransactionalUnitOfWorkPort`, `DomainEventBusPort`, `IdempotencyStorePort`, `DistributedLockPort`, `ObjectStoragePort`, `VirusScannerPort`, `PiiVaultPort`, `TaskQueueDispatcherPort`, `RealtimeProgressPublisherPort`, `ObservabilityTracerPort` |
| **Recruitment Management** | `application/recruitment/` | `CreateJobRequisitionUseCase`, `PublishJobRequisitionUseCase`, `UpdateJobRubricUseCase`, `LockJobRequisitionRubricUseCase`, `ArchiveJobRequisitionUseCase`, `GetJobRequisitionUseCase`, `LockRubricOnBatchOrInterviewStartedHandler` |
| **Document Intelligence** | `application/document_intelligence/` | `InitiateScreeningBatchUseCase`, `ProcessDocumentIngestionTaskUseCase`, `ExecuteHybridMatchingUseCase`, `GetScreeningBatchStatusUseCase`, `CandidateProfileReadModelPort`, `DocumentParserPort`, `HybridVectorSearchPort` |
| **Live Adaptive Interview** | `application/live_interview/` | `InitializeInterviewSessionUseCase`, `ProcessCandidateTurnUseCase`, `RecordClientTelemetryUseCase`, `TerminateInterviewEarlyUseCase`, `CompleteInterviewSessionUseCase`, `AudioSttPort`, `AudioTtsPort`, `PromptGuardrailPort`, `TokenBudgetEnforcerPort`, `AdaptiveInterviewGraphPort` |
| **Evaluation & Analytics** | `application/evaluation_analytics/` | `GenerateEvaluationReportUseCase`, `RecordRecruiterOverrideUseCase`, `BuildCandidateComparisonMatrixUseCase`, `TriggerEvaluationReportOnInterviewCompletedHandler` |
| **Identity & Governance** | `application/identity_governance/` | `ProvisionTenantAccountUseCase`, `RecordCandidateConsentUseCase`, `RevokeCandidateConsentUseCase`, `ExecuteCandidateErasureUseCase` |
| **Composition Root** | `composition_root/` | `ApplicationContainer`, `RepositoryRegistry`, `ExternalAdapterRegistry`, `ApplicationContainerPort`, `Phase3PipelineBundle`, `Phase4PipelineBundle`, `Phase5PipelineBundle`, `Phase6PipelineBundle`, `build_phase3_container`, `build_phase4_container`, `build_phase5_container`, `build_phase6_container`, `build_sql_repositories` |

### Phase 3 Document Intelligence, PII Vault & Hybrid Vector Adapters (`infrastructure/`)

| Subsystem | Package Path | Implemented Adapters, ACLs & Protocols |
| :--- | :--- | :--- |
| **Security & PII Vault (`ADR-008`)** | `infrastructure/security/` | `SignatureAndHeuristicVirusScannerAdapter` (PDF/DOCX/UTF-8 magic bytes, EICAR, VBA macro, Zip Slip/bomb guards), `CryptographicPiiVaultAdapter`, `StdlibHmacCtrEnvelopeCipher`, `EnvelopeCipherProtocol` ($O(1)$ `crypto_shred`) |
| **Object Storage & Vector Backend** | `infrastructure/persistence/` | `TenantScopedObjectStorageAdapter` (`{tenant_id}/{batch_id}/{sha256}`, signed presigned URLs), `InMemoryHybridVectorStoreBackend`, `VectorStoreBackendProtocol`, `InMemoryIdempotencyStore`, `InMemoryDistributedLock` |
| **Resilience & DLQ Messaging** | `infrastructure/messaging/` | `ExponentialBackoffRetryPolicy` ($T_{\text{sleep}}(k) = \text{Uniform}(0, \min(T_{\max}, T_{\text{base}} \cdot 2^k))$), `classify_failure`, `CircuitBreaker`, `ResilientTaskExecutor`, `InMemoryDeadLetterQueue` (`DeadLetterQueuePort`) |
| **LangChain Extraction ACL** | `infrastructure/ai_adapters/langchain_parser/` | `DocumentTextNormalizer` (PDF `FlateDecode`/text, DOCX XML with DTD/XXE guard, NFKC + invisible char stripping), `GroundingVerifier` ($\tau_{\text{grounding}} = 0.85$ + chronological overlap), `LangChainDocumentParserAdapter`, `StructuredExtractionRunnableProtocol`, `DeterministicRuleBasedExtractorClient` |
| **Hierarchical Chunking & Hybrid Search (`ADR-004`)** | `infrastructure/ai_adapters/vector_store/` | `HierarchicalDocumentChunker` (Parent sections + overlapping Child passages with deterministic `UUIDv5`), `Bm25SparseEncoder` ($k_1=1.5, b=0.75$), `DenseVectorMath`, `DenseEmbeddingClientProtocol`, `HashProjectionDenseEmbeddingClient`, `HybridVectorSearchAdapter` |

### Phase 4 Stateful Adaptive Interview Engine, Security Guardrails & Audio ACLs (`infrastructure/`)

| Subsystem | Package Path | Implemented Adapters, Nodes & Protocols |
| :--- | :--- | :--- |
| **4-Plane LangGraph State & Checkpointer (`ADR-005`, `ADR-009`)** | `infrastructure/ai_adapters/langgraph_interview/` | `SessionMetadataPlane`, `RubricContextPlane`, `AdaptiveRoutingPlane`, `DialogueLedgerPlane`, `InterviewGraphState`, idempotent `StateReducer` (bounded `recent_dialogue_window`), `InterviewStateCheckpointerProtocol`, `InMemoryFencedStateCheckpointer` |
| **Cognitive Nodes & Routing Engine** | `infrastructure/ai_adapters/langgraph_interview/` | `QuestionGeneratorNode` (`PromptHash` dedup), `EvaluationNode` (`<untrusted_candidate_response>` XML boundary isolation & stylometric risk), `SocraticFollowUpNode`, `ScoringVerificationNode`, `AdaptiveEdgeRouter` (P1–P6 rules), `LangGraphAdaptiveInterviewAdapter` |
| **Adversarial Prompt Guardrail (`ADR-012`)** | `infrastructure/security/prompt_guardrail.py` | `AdversarialPromptGuardrailAdapter` ($\mathcal{R}_{\text{adv}}$ risk bands, Unicode/NFKC & zero-width smuggling neutralization, Base64/Hex encoded attack decoding, delimiter & instruction-override blocking) |
| **FinOps Token Budget & Fencing Lock (`ADR-013`)** | `infrastructure/ai_adapters/langgraph_interview/`, `infrastructure/messaging/` | `SlidingWindowTokenBudgetEnforcerAdapter` (session ceilings, sliding-window burst caps, micro-USD tracking), `FencingTokenDistributedLockAdapter` (monotonic epochs, TTL reclamation, stale-release isolation) |
| **Audio Speech ACLs** | `infrastructure/ai_adapters/audio_adapters.py` | `StreamingAudioSttAdapter`, `StreamingAudioTtsAdapter`, `SttDriverProtocol`, `TtsDriverProtocol`, silence/RMS energy validation & `[0.0, 600.0]` WPM clamping |

### Phase 5 Presentation API Catalog, Streaming Workers & WebSocket Protocol (`presentation/`, `infrastructure/messaging/`)

| Subsystem | Package Path | Implemented Routers, Middleware, Brokers & Gateways |
| :--- | :--- | :--- |
| **RESTful API Catalog & Envelopes** | `presentation/rest/`, `presentation/schemas/` | `RestApiRouter` (20 v1 endpoints across all 5 Bounded Contexts), `RecruitmentController`, `DocumentIntelligenceController`, `LiveInterviewController`, `EvaluationController`, `IdentityController`, `ApiResponseEnvelope[T]`, `ProblemDetailsResponse` (RFC 9457) |
| **Security, Idempotency & Error Middleware** | `presentation/middleware/`, `presentation/exceptions.py` | `BearerTokenAuthenticator` (HS256 HMAC-SHA256 JWT, RBAC scopes, cross-tenant `403`), `ExceptionToProblemDetailsTranslator` (`400`, `401`, `403`, `404`, `405`, `409`, `422`, `429`, `502`, `503`), `IdempotencyMiddleware` (`Idempotency-Key` + SHA-256 fingerprint), `TenantRateLimiter` (`X-RateLimit-*`, `Retry-After`) |
| **Priority Token Bucket, WFQ Worker & SSE (`ADR-006`)** | `infrastructure/messaging/` | `PriorityReservedTokenBucketLimiter` (`CRITICAL_INTERVIEW`, `INTERACTIVE_MATCHING`, `BULK_INGESTION` reserve floors), `BatchIngestionWorkerCoordinator` (WFQ tenant fair-queuing, Little's Law semaphore, `ResilientTaskExecutor`), `ReplayableSseProgressBroker` (`Last-Event-ID` ring-buffer replay) |
| **Full-Duplex WebSocket Gateway (`ADR-007`)** | `presentation/websocket/` | `WebSocketInterviewGateway` (`/v1/interviews/{session_id}/ws`, `session.ready`, `session.state_sync` reconnect recovery, `audio.chunk`, `candidate.utterance`, `candidate.barge_in`, `telemetry.snapshot`), `compute_adaptive_silence_threshold_ms` ($T_{\text{silence}}$), `BargeInStreamController` (`tts.flush_interrupt`) |

### Phase 6 Persistence Engine, Transactional Outbox, INT8 Vector Quantization & Checkpointing (`infrastructure/persistence/`)

| Subsystem | Package Path | Implemented Repositories, Outbox, Quantizer & Checkpointer |
| :--- | :--- | :--- |
| **Relational Schema & Data Mappers** | `infrastructure/persistence/relational/` | `SqlDialect` (`SQLITE`/`POSTGRESQL` DDL across 17 tables), `SqlDatabaseManager` (WAL mode & foreign keys), lossless bidirectional mappers (`row_to_*`, `*_to_rows`), `SqlIdempotencyStoreAdapter`, `SqlCandidatePiiVaultStore` (`ADR-008`) |
| **ACID Repositories, OCC & Outbox UoW** | `infrastructure/persistence/relational/` | All 8 `Sql*Repository` implementations + `CandidateProfileReadModelPort` enforcing `tenant_id` scoping & `version = version + 1` OCC (`OPTIMISTIC_CONCURRENCY_CONFLICT`), `SqlTransactionalUnitOfWork` atomically writing `transactional_outbox` rows via `OutboxEventCodec` |
| **INT8 Quantization & Anti-Entropy Sync** | `infrastructure/persistence/vector_store/` | `Int8ScalarQuantizer` (4x memory reduction, oversampled $k_{\text{oversample}}$ + `float32` rescoring), `QuantizedHybridVectorStoreBackend` (`sync_version` monotonicity), `TransactionalOutboxRelayWorker` (retry + DLQ), `CrossStoreAntiEntropyReconciler` |
| **Two-Tier Fenced Checkpointer (`ADR-009`)** | `infrastructure/persistence/checkpointing/` | `TwoTierFencedStateCheckpointer` (TTL L1 hot cache + L2 durable `interview_state_checkpoints`, SHA-256 `InterviewStateSerializer` tamper detection, monotonic fencing rejection `STALE_FENCING_TOKEN`) |

### Phase 7 Frontend Clean Architecture & Next.js App Router Portal (`frontend/`)

| Layer | Package Path | Implemented State Machines, Coordinators, Adapters & Routes |
| :--- | :--- | :--- |
| **Domain Layer (Pure TS)** | `frontend/src/domain/` | 13-state `transitionInterviewFsm` (`INTERVIEW_FSM_STATES`), `PcmRingBuffer` & `VoiceActivityHysteresisTracker` (280ms VAD + $t_{\text{played\_ms}}$ barge-in math), `assessPasteAndKeystrokeAnomalies` / `computeFocusAnomalyScore` / `computeCadenceAnomalyScore` (accommodated text mode `0` exemption), `computeEmpiricalBayesShrinkage` / `computePosteriorCompetencyScore` / `computeHolisticDecisionIndex` / `buildNormalizedComparisonMatrix` (2–4 finalists) |
| **Application Layer (Ports & Coordinators)** | `frontend/src/application/` | Segregated Outbound Ports (`RestApiClientPort`, `InterviewWebSocketPort`, `BatchSseStreamPort`, `AudioEnginePort`, `IntegrityTelemetryCollectorPort`), `ObservableStore<T>`, `LiveInterviewSessionCoordinator`, `BatchMonitorCoordinator` (500-file manifest validator), `ComparisonMatrixCoordinator` |
| **Infrastructure Adapters** | `frontend/src/infrastructure/` | `FetchRestApiClientAdapter` (JWT, `Idempotency-Key`, RFC 9457 `ProblemDetailsError`), `ReconnectingInterviewWebSocketAdapter` (exponential backoff + `session.join`), `RafCoalescedBatchSseAdapter` (60 FPS `requestAnimationFrame` delta coalescing + `Last-Event-ID`), `WebAudioRingBufferAdapter`, `BrowserIntegrityTelemetryAdapter` |
| **Composition, Hooks & App Router UI** | `frontend/src/composition/`, `frontend/src/presentation/`, `frontend/src/app/` | `createFrontendServices`, `AppServicesProvider`, `useInterviewController`, `useBatchMonitor`, `useComparisonMatrix`, `InterviewPortalView`, `BatchMonitorView`, `ComparisonMatrixView`, routes `/`, `/interview/[sessionId]`, `/recruiter/batches/[batchId]`, `/recruiter/requisitions/[requisitionId]/compare` |

### Phase 8 AI Security Firewall, OTel Observability, FinOps, Rubric Drift & HA Topology

| Subsystem | Package / File Path | Implemented Security, Observability, Scaling & Deployment Blocks |
| :--- | :--- | :--- |
| **Defense-in-Depth Security (`ADR-012`)** | `infrastructure/security/defense_in_depth.py` | `NonceDelimiterEnvelope` (`<untrusted_candidate_input_{nonce}>` breakout protection), `CanaryTokenLeakVerifier` (`SYSTEM_PROMPT_CANARY_LEAK` quarantine & fallback), `DualLlmQuarantineExtractor` (unprivileged Reader + grounded Judge), `AdversarialStrikeTracker` (3-strike + critical $\ge 0.85$ early termination), `DefenseInDepthFirewall` |
| **OTel GenAI Tracer & PII Scrubber (`ADR-013`)** | `infrastructure/observability/otel_tracer.py` | `StructuredOpenTelemetryTracerAdapter` fulfilling `ObservabilityTracerPort`, W3C `traceparent` (`parse_traceparent`) parent-child nesting, automatic PII/token scrubbing (`redact_pii_text`), $P_{50}/P_{95}/P_{99}$ latency percentile telemetry |
| **FinOps Cost Attribution & Tier Router** | `infrastructure/observability/finops_governance.py` | `MultiTenantFinOpsController` tracking integer `cost_micro_usd` + prefix cache hit ratio $\eta_{\text{cache}}$, auto-downgrading to `COST_OPTIMIZED_FAST_MODEL` at 80% quota and tripping `FINOPS_HARD_BUDGET_EXCEEDED` at 100% |
| **Statistical Rubric Drift & Fairness** | `infrastructure/observability/rubric_drift_monitor.py` | `compute_psi`, `compute_psi_from_scores` (`STABLE`/`MODERATE_DRIFT`/`CRITICAL_DRIFT`), `compute_ks_two_sample` ($D_{\text{KS}}$ vs. critical value), `compute_cohens_kappa` (unweighted $\kappa$ + quadratic weighted $\kappa_w$, `RUBRIC_CALIBRATION_DEGRADED` alert at $< 0.70$), `RubricDriftMonitor` |
| **Stateless WS Backplane, Autoscaler & Ops API** | `infrastructure/messaging/`, `presentation/` | `StatelessWebSocketPubSubBackplane` (cross-node routing + `initiate_graceful_drain`), `QueueDepthWorkerAutoscaler` & `compute_ws_gateway_replicas`, `OpsController` & `RestApiRouter` routes (`/health/live`, `/health/ready`, `/v1/ops/metrics`), `Phase8PipelineBundle` / `build_production_container()` |
| **Containers & CI/CD** | `backend/Dockerfile`, `frontend/Dockerfile`, `docker-compose.production.yml`, `.github/workflows/ci.yml` | Multi-stage non-root Python 3.12 & Node 22 images, full Postgres + Redis + Qdrant + API + Worker + Frontend Compose topology, automated Ruff / Mypy / Import-Linter / Pytest & TypeScript / Vitest CI pipeline |

---

## Implementation Roadmap & Status

- [x] **Phase 1: Core Domain Layer, Tactical DDD Building Blocks & Outbound Port Contracts** — **Complete**
  - Shared Domain Kernel with zero external dependencies and RFC 9562 UUIDv7 generation
  - All 5 Bounded Contexts with mathematical invariants, Domain Services, Events, and Repository Protocols
  - Typed Outbound Application Ports & DTO contracts (`DocumentParserPort`, `HybridVectorSearchPort`, `AdaptiveInterviewGraphPort`, `TransactionalUnitOfWorkPort`)
  - Unit tests, AST Clean Architecture fitness suite, `mypy --strict`, `ruff`, and `import-linter` contracts
- [x] **Phase 2: Clean Architecture Application Layer, SOLID Governance & Cross-Context Event Choreography** — **Complete**
  - Zero-framework `application/` layer with 22 single-responsibility Use Cases, segregated Inbound/Outbound `Protocol` Ports, and frozen DTO Anti-Corruption boundaries
  - Cross-context event choreography (`LockRubricOnBatchOrInterviewStartedHandler`, `TriggerEvaluationReportOnInterviewCompletedHandler`) via `InMemoryDomainEventBus`
  - Pure manual Dependency Injection in `ApplicationContainer` (`composition_root/`) and tenant-scoped `InMemoryUnitOfWork`
  - SOLID fitness & Robert C. Martin Coupling Metrics ($C_a, C_e, I, A, D$) verification (`test_solid_and_coupling_metrics.py`) alongside `import-linter`
- [x] **Phase 3: Document Intelligence, Anti-Hallucination ACL, Cryptographic PII Vault & Hybrid Vector Search** — **Complete**
  - Malware/magic-byte quarantine (`SignatureAndHeuristicVirusScannerAdapter`), tenant-scoped `TenantScopedObjectStorageAdapter`, and envelope-encrypted `CryptographicPiiVaultAdapter` with $O(1)$ `crypto_shred()`
  - Layout-aware PDF/DOCX/Text `DocumentTextNormalizer`, anti-hallucination `GroundingVerifier`, and PII-free `LangChainDocumentParserAdapter`
  - `ADR-004` Parent-Child `HierarchicalDocumentChunker` (deterministic `UUIDv5`), `Bm25SparseEncoder` + dense cosine `HybridVectorSearchAdapter` with strict `tenant_id` isolation & GDPR purge
  - Full-jitter exponential backoff (`ExponentialBackoffRetryPolicy`), `CircuitBreaker`, `InMemoryDeadLetterQueue`, and end-to-end integration pipeline wired via `build_phase3_container()`
- [x] **Phase 4: Stateful Adaptive Interview Engine, Cognitive Nodes, Guardrails & Fenced Checkpointing** — **Complete**
  - `ADR-005` 4-Plane `InterviewGraphState` with bounded idempotent `StateReducer` and `ADR-009` tenant-isolated `InMemoryFencedStateCheckpointer`
  - Cognitive nodes (`QuestionGeneratorNode`, `EvaluationNode`, `SocraticFollowUpNode`, `ScoringVerificationNode`) behind injectable model Protocols and `<untrusted_candidate_response>` XML boundary isolation
  - `AdversarialPromptGuardrailAdapter`, `SlidingWindowTokenBudgetEnforcerAdapter`, `FencingTokenDistributedLockAdapter`, and `StreamingAudioSttAdapter` / `StreamingAudioTtsAdapter`
  - End-to-end adaptive interview Scenarios A/B/C (mastery escalation, Socratic $N_{\text{probe}} \le 2$ enforcement, telemetry + injection high-risk alert & automatic `EvaluationReport` synthesis) wired via `build_phase4_container()`
- [x] **Phase 5: RESTful API Catalog, RFC 9457 Error Governance, Priority Batch Orchestration, SSE & WebSocket Protocol** — **Complete**
  - Clean Architecture `presentation/` layer isolated from `infrastructure/` (`import-linter` 5/5 contracts kept + AST dependency & coupling fitness tests)
  - All 20 REST endpoints across Recruitment, Document Intelligence, Live Interview, Evaluation, and Identity Governance guarded by HS256 JWT auth, RBAC scopes, cross-tenant `403` isolation, SHA-256 idempotency replay/conflict detection, and per-tenant HTTP rate limits
  - `ADR-006` `PriorityReservedTokenBucketLimiter`, `BatchIngestionWorkerCoordinator` (WFQ + Little's Law concurrency), and `ReplayableSseProgressBroker` supporting `Last-Event-ID` resumption
  - `ADR-007` `WebSocketInterviewGateway` with reconnection delta recovery (`session.state_sync`), adaptive end-of-turn silence ($T_{\text{silence}}$), and preemptive `BargeInStreamController` TTS cancellation wired via `build_phase5_container()`
- [x] **Phase 6: Relational Persistence Engine, Data Mappers, Transactional Outbox, INT8 Vector Store & Two-Tier Fenced Checkpointer** — **Complete**
  - Dual PostgreSQL/SQLite DDL schema (17 relational tables) executed on ACID WAL-enabled `SqlDatabaseManager`
  - All 8 `Sql*Repository` adapters + CQRS `CandidateProfileReadModelPort`, `SqlIdempotencyStoreAdapter`, and SQL-backed `SqlCandidatePiiVaultStore` (`ADR-008`) enforcing strict `tenant_id` isolation and Optimistic Concurrency Control (`OPTIMISTIC_CONCURRENCY_CONFLICT`)
  - `SqlTransactionalUnitOfWork` atomically committing aggregate mutations and `transactional_outbox` events, relayed asynchronously by `TransactionalOutboxRelayWorker` with DLQ protection
  - `Int8ScalarQuantizer` + `QuantizedHybridVectorStoreBackend` (versioned `sync_version` writes, oversampled INT8 pre-filter + `float32` rescoring), `CrossStoreAntiEntropyReconciler` (healing missing/stale/orphan/erasure drift), and `TwoTierFencedStateCheckpointer` (L1 cache + SHA-256-verified SQL L2 + monotonic fencing) wired via `build_phase6_container()`
- [x] **Phase 7: Frontend Clean Architecture, 13-State Interview FSM, Telemetry & Next.js App Router Portal** — **Complete**
  - Strict TypeScript `ADR-010` 4-layer Next.js 15 App Router frontend (`domain/`, `application/`, `infrastructure/`, `presentation/` + `composition/` container) enforced by TypeScript AST architectural boundary tests
  - 13-state Candidate Interview Portal FSM (`transitionInterviewFsm`), Web Audio `PcmRingBuffer`, 280ms VAD hysteresis (`VoiceActivityHysteresisTracker`), and exact wall-clock barge-in offset truncation ($t_{\text{played\_ms}}$)
  - Non-intrusive client telemetry pipeline (`assessPasteAndKeystrokeAnomalies`, $S_{\text{focus}}$, $S_{\text{cadence}}$) guaranteeing zero cadence penalty under Accommodated Text Mode
  - `ADR-011` `RafCoalescedBatchSseAdapter` coalescing rapid SSE task deltas at 60 FPS, Empirical Bayes Candidate Comparison Matrix (`buildNormalizedComparisonMatrix`), Blind Hiring mode toggle, Evidence Drilldown Drawer, and auditable Recruiter Overrides
- [x] **Phase 8: AI Security Defense-in-Depth, OpenTelemetry Observability, FinOps Governance, Rubric Drift & Production Topology** — **Complete**
  - `ADR-012` 4-layer defense pipeline (`NonceDelimiterEnvelope`, `CanaryTokenLeakVerifier`, `DualLlmQuarantineExtractor`, `AdversarialStrikeTracker`, `DefenseInDepthFirewall`)
  - `ADR-013` W3C TraceContext OpenTelemetry GenAI tracer with automated PII scrubbing (`StructuredOpenTelemetryTracerAdapter`), 80%/100% FinOps model tier routing (`MultiTenantFinOpsController`), and formal statistical rubric drift & calibration monitoring (`compute_psi`, `compute_ks_two_sample`, `compute_cohens_kappa`, `RubricDriftMonitor`)
  - `StatelessWebSocketPubSubBackplane` with turn-boundary graceful node drain + L2 SQL checkpoint failover, hysteresis `QueueDepthWorkerAutoscaler`, and operational `/health/live`, `/health/ready`, `/v1/ops/metrics` REST endpoints wired via `build_production_container()`
  - Production multi-stage non-root Dockerfiles (`backend/Dockerfile`, `frontend/Dockerfile`), `docker-compose.production.yml`, and `.github/workflows/ci.yml`
- [x] **Phase 9: Live ASGI/HTTP/SSE/WebSocket Runtime, Pluggable Live LLM Provider Bridge, Idempotent Interactive Demo Seeder & Full-Stack Command Center** — **Complete (`v1.0.0`)**
  - Zero-dependency ASGI 3.0 application + standalone pure-stdlib `AsyncStdlibHttpWsServer` (`presentation/server.py`) serving CORS-enabled REST, SSE (`text/event-stream`), RFC 6455 full-duplex WebSockets (`/v1/interviews/{session_id}/ws`), OpenAPI 3.1.0 (`/openapi.json`), and interactive Swagger UI (`/docs`)
  - Pluggable `OpenAiCompatibleJsonLlmClient` (`live_llm_provider.py`) supporting Groq (`GROQ_API_KEY` / `llama-3.3-70b-versatile`), OpenAI (`OPENAI_API_KEY` / `gpt-4o-mini`), Google Gemini (`GEMINI_API_KEY` / `gemini-2.0-flash`), or any custom OpenAI-compatible `LLM_BASE_URL` endpoint — with automatic Zero-Key Deterministic Fallback
  - Idempotent `seed_interactive_demo_environment()` (`demo_seeder.py`) provisioning **Acme AI Engineering**, a published **Senior AI Systems & Full-Stack Engineer** requisition (Python & TypeScript gatekeepers), 4 processed CVs (3 finalists + 1 gatekeeper-rejected), 3 completed L2..L5 `EvaluationReport`s, 1 active `IN_PROGRESS` Turn 1 session, plus live background batch simulation (`POST /v1/ops/demo-simulate-batch`) & manifest bootstrap (`GET /v1/ops/demo-manifest`)
  - Live Next.js 15 Showcase integration (`CommandCenterView`, `InterviewPortalView`, `BatchMonitorView`, `ComparisonMatrixView`) with `/api/*` rewrite proxying, live readiness/FinOps/Drift panels, and one-click SSE batch simulation

---

## Quickstart & Live Interactive Demo (`v1.0.0`)

Experience the entire platform locally in under 60 seconds with **Zero API Keys Required** (Deterministic Mode), or attach a Groq/OpenAI/Gemini key for live cloud reasoning.

### 1. Zero-Key Deterministic Showcase Mode (Instant Local Launch)

```bash
cp .env.example .env

# Terminal 1 — Boot ASGI/HTTP/SSE/WebSocket backend + seed interactive Acme AI demo (http://127.0.0.1:8000)
make dev-backend

# Terminal 2 — Boot Next.js 15 Command Center (http://127.0.0.1:3000)
make dev-frontend
```

Once running:
- **Executive Command Center:** `http://127.0.0.1:3000` (auto-bootstraps `/v1/ops/demo-manifest`, displays live FinOps/Drift metrics, and provides 1-click links to the active Turn 1 session, SSE Batch Monitor, and Candidate Comparison Matrix)
- **Interactive Swagger UI:** `http://127.0.0.1:8000/docs` (plus raw spec at `/openapi.json`)
- **Pre-seeded Demo Manifest:** `http://127.0.0.1:8000/v1/ops/demo-manifest`

### 2. Optional Live Cloud LLM Mode (Groq / OpenAI / Gemini)

Edit `.env` (or export environment variables) with any compatible provider key:

```bash
# Option A: Groq Cloud (fastest inference)
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here
LLM_MODEL=llama-3.3-70b-versatile

# Option B: OpenAI
# LLM_PROVIDER=openai
# OPENAI_API_KEY=sk-your_key_here
# LLM_MODEL=gpt-4o-mini

# Option C: Google Gemini (OpenAI-compatible endpoint)
# LLM_PROVIDER=gemini
# GEMINI_API_KEY=your_gemini_key_here
# LLM_MODEL=gemini-2.0-flash
```

Restart `make dev-backend` — `OpenAiCompatibleJsonLlmClient` routes CV extraction, question generation, Socratic probing, and synthesis through your provider while automatically falling back to deterministic engines on timeout or rate limits.

---

## Architectural Blueprints (`docs/architecture/`)

1. [Phase 1: System Vision, PRD, Strategic & Tactical DDD Blueprint](docs/architecture/phase_1_system_vision_prd_strategic_and_tactical_ddd_blueprint.md)
2. [Phase 2: Clean Architecture Blueprint, SOLID Matrix & Component Mapping](docs/architecture/phase_2_clean_architecture_blueprint_solid_matrix_and_component_mapping.md)
3. [Phase 3: Document Intelligence & Semantic Matching Architecture](docs/architecture/phase_3_document_intelligence_and_semantic_matching_architecture.md)
4. [Phase 4: Stateful Adaptive Interview Engine Design](docs/architecture/phase_4_stateful_adaptive_interview_engine_design.md)
5. [Phase 5: API Contracts, Async Task Orchestration & Real-Time WebSocket Protocol](docs/architecture/phase_5_api_contracts_async_task_orchestration_and_real_time_websocket_protocol.md)
6. [Phase 6: Persistence Architecture, Data Governance & State Checkpointing](docs/architecture/phase_6_persistence_architecture_data_governance_and_state_checkpointing.md)
7. [Phase 7: Frontend Clean Architecture, State Synchronization & UX Flows](docs/architecture/phase_7_frontend_clean_architecture_state_synchronization_and_ux_flows.md)
8. [Phase 8: AI Security, Guardrails, Observability & Production Topology](docs/architecture/phase_8_ai_security_guardrails_observability_and_production_topology.md)

---

## Running Tests & Architectural Verification

### Backend (`backend/`)

From the repository root, create a Python 3.12+ virtual environment and install development tools:

```bash
python3 -m venv .venv
.venv/bin/pip install -e "backend[dev]"
```

Run the complete backend quality and architectural fitness gate from `backend/`:

```bash
cd backend

# 1. Unit & AST Clean Architecture tests with branch coverage
../.venv/bin/pytest

# 2. Strict static type verification (Python 3.12+)
../.venv/bin/mypy --strict src tests

# 3. Linting and style verification
../.venv/bin/ruff check src tests

# 4. Import-Linter layer, purity, and bounded context isolation contracts
PYTHONPATH=src ../.venv/bin/lint-imports
```

### Frontend (`frontend/`)

Requires Node.js 20+:

```bash
cd frontend
npm ci

# 1. Strict TypeScript check (`strict`, `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`)
npm run typecheck

# 2. Unit, Integration & TypeScript AST Clean Architecture boundary tests with V8 coverage
npm run coverage

# 3. Production Next.js App Router bundle check
npm run build
```

### Production Stack Deployment (`docker-compose.production.yml`)

```bash
docker compose -f docker-compose.production.yml up --build -d
```

Verified monorepo baseline metrics:
- **Backend:** `96 passed` across unit, integration & Clean Architecture/SOLID fitness tests (`90.21%` combined statement+branch coverage), `mypy --strict` passing across `255 source files`, `ruff` passing, and `lint-imports` (`5 kept, 0 broken`).
- **Frontend:** `tsc --noEmit` (`strict`, `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`), Next.js 15 production build (`4/4` routes), and Vitest suite (`5 test files, 19 tests passed`, including TypeScript AST Clean Architecture boundary enforcement).
