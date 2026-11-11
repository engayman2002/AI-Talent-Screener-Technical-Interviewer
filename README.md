# AI Talent Screener & Technical Interviewer

![Phase 1](https://img.shields.io/badge/Phase%201-Complete-brightgreen)
![Phase 2](https://img.shields.io/badge/Phase%202-Complete-brightgreen)
![Architecture](https://img.shields.io/badge/Architecture-Clean%20Architecture%20%2B%20DDD-blue)
![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB)
![Typing](https://img.shields.io/badge/mypy--strict-passing-success)

An enterprise **AI Talent Screener & Stateful Adaptive Technical Interviewer** engineered around **Clean Architecture**, **Hexagonal Ports & Adapters**, and **Strategic & Tactical Domain-Driven Design (DDD)**.

---

## Executive Architectural Overview

Manual high-volume technical screening breaks down under heterogeneous resume formats, keyword-based ATS false negatives, resume exaggeration, and hundreds of repetitive L1 phone screens. This platform separates deterministic business policies, mathematical scoring, and cognitive state machines from volatile AI frameworks (`LangChain`, `LangGraph`, LLM vendors, vector stores) across strict concentric boundaries:

1. **Domain Layer (`backend/src/talent_screener/domain/`):** 100% pure Python Standard Library core owning Aggregate Roots, Entities, immutable Value Objects, Domain Services, Domain Events, and Repository Protocols across all **5 Bounded Contexts**.
2. **Application Layer (`backend/src/talent_screener/application/`):** 100% pure Python Standard Library orchestration layer containing **22 Single-Responsibility Use Case Interactors**, segregated Inbound & Outbound `Protocol` Ports, immutable `@dataclass(frozen=True, slots=True)` Request/Response DTO boundaries, and cross-context Domain Event choreography handlers.
3. **Composition Root & In-Memory Adapters (`backend/src/talent_screener/composition_root/`, `infrastructure/`):** Pure Manual Dependency Injection (`ApplicationContainer`), `InMemoryDomainEventBus`, and tenant-scoped `InMemoryUnitOfWork` / repository implementations for fast deterministic testing.
4. **External Infrastructure & Presentation Layers (Upcoming Phases):** Concrete `LangChain` / `LangGraph` Anti-Corruption Layers (ACLs), PostgreSQL + PII Vault crypto-shredding persistence, hybrid vector search, FastAPI REST/SSE, and bi-directional WebSocket voice/text gateways.

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
| **Composition Root** | `composition_root/` | `ApplicationContainer`, `RepositoryRegistry`, `ExternalAdapterRegistry`, `ApplicationContainerPort` |

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
  - SOLID fitness & Robert C. Martin Coupling Metrics ($C_a, C_e, I, A, D$) verification (`test_solid_and_coupling_metrics.py`) alongside `import-linter` (4/4 contracts kept)
- [ ] **Phase 3:** Persistence Adapters, PostgreSQL Data Mappers, Cryptographic PII Vault & Transactional Outbox
- [ ] **Phase 4:** LangChain Extraction & Hybrid Vector Search Adapters + LangGraph Adaptive Interview Graph
- [ ] **Phase 5:** FastAPI REST, SSE Batch Telemetry & Full-Duplex WebSocket Voice/Text Gateway
- [ ] **Phase 6:** Next.js App Router Frontend Workspace & Live Interview Portal

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

From the repository root, create a Python 3.12+ virtual environment and install development tools:

```bash
python3 -m venv .venv
.venv/bin/pip install -e "backend[dev]"
```

Run the complete quality and architectural fitness gate from `backend/`:

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
