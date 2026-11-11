# AI Talent Screener & Technical Interviewer

## Phase 5: API Contracts, Async Task Orchestration & Real-Time WebSocket Protocol

* **Document ID:** `ARCH-SPEC-PHASE-05`
* **Architectural Paradigm:** Clean Architecture (Presentation & Infrastructure Delivery Boundaries), Event-Driven Asynchronous Task Orchestration, Full-Duplex Stateful WebSocket Protocol
* **Implementation Policy:** Strict Zero-Code Conceptual & Mathematical Specification
* **Bounded Contexts Covered:** All Contexts (*Recruitment Management*, *Document Intelligence*, *Live Interview*, and *Evaluation & Analytics*)

---

## 1. RESTful API Architectural Catalog & Delivery Governance

### 1.1 Clean Architecture Presentation Layer Governance

In strict compliance with Robert C. Martin's **Clean Architecture**, the FastAPI Presentation Layer functions exclusively as an **External Delivery Mechanism**. HTTP Routers and Controllers are restricted to four responsibilities:

1. **Transport Deserialization & Structural Validation:** Validating incoming HTTP headers, path parameters, query parameters, and request body payloads against strict Presentation Layer Request Contracts.
2. **Authentication, Tenant Isolation & RBAC Enforcement:** Extracting cryptographic identity claims (OAuth2/OIDC JWTs for Recruiters and Hiring Managers; short-lived signed Session Tickets for Candidates) and binding the immutable `TenantID` and `ActorRole` to the execution context.
3. **Inbound Port Invocation:** Translating validated HTTP requests into immutable **Application Command or Query DTOs** and invoking the corresponding **Application Layer Inbound Driving Port** (Use Case Interactor).
4. **Transport Serialization & RFC 9457 Error Mapping:** Mapping Application Result DTOs or Domain Exceptions into standardized HTTP status codes and conceptual response envelopes without leaking internal stack traces, ORM structures, or AI framework internals.

### 1.2 Enterprise RESTful API Endpoint Catalog

All REST endpoints are versioned under `/api/v1/` and enforce strict multi-tenant data isolation via the authenticated `TenantID` claim.

#### Table 1.2.1: Recruitment Management & Document Intelligence Endpoints

| Endpoint URI & Verb | Auth Scope & RBAC | Idempotency & Rate Limit Policy | Conceptual Request Contract | Conceptual Response & Status |
| :--- | :--- | :--- | :--- | :--- |
| `POST /api/v1/requisitions` | `recruiter:write` `hiring_mgr:write` | **Idempotent** via `Idempotency-Key` header ($24\text{h}$ TTL); $30\text{ req/min}$ per tenant. | `CreateRequisitionRequest`: Title, RawJDText, SeniorityBand, GatekeeperRules, WeightedCompetencies. | `201 Created`: `RequisitionSummaryResponse` with normalized `JobCompetencyRubric` and `RequisitionID`. |
| `GET /api/v1/requisitions/{req_id}` | `recruiter:read` `hiring_mgr:read` | **Safe / Idempotent**; $300\text{ req/min}$ per user. | Path: `req_id` (`UUIDv7`). Query: `include_rubric_anchors` (`Boolean`). | `200 OK`: `RequisitionDetailResponse` including active rubric version and pipeline counts. |
| `PUT /api/v1/requisitions/{req_id}/rubric` | `hiring_mgr:write` | **Idempotent** via `If-Match` ETag optimistic concurrency header; $20\text{ req/min}$. | `UpdateCompetencyRubricRequest`: DimensionWeights ($\sum w_k = 1.0$), TargetMasteryLevels, Gatekeepers. | `200 OK`: `RubricVersionResponse` with incremented immutable `RubricVersionNumber`. |
| `POST /api/v1/requisitions/{req_id}/batches` | `recruiter:write` | **Idempotent** via `Idempotency-Key` header; $10\text{ batches/min}$ per tenant (Max $500$ files/batch). | `InitiateScreeningBatchRequest`: BatchLabel, FileManifestList (FileName, ByteSize, SHA256Hash, MimeType). | `202 Accepted`: `BatchInitiatedResponse` containing `BatchID`, PreSignedUploadURIs, and `SSEStreamURI`. |
| `POST /api/v1/batches/{batch_id}/commit` | `recruiter:write` | **Idempotent** state transition; $20\text{ req/min}$ per tenant. | `CommitBatchUploadsRequest`: List of uploaded `DocumentID` confirmations and ETag checksums. | `202 Accepted`: `BatchProcessingStartedResponse` with estimated completion SLA timestamp. |
| `GET /api/v1/batches/{batch_id}/events` | `recruiter:read` | **Server-Sent Events (`text/event-stream`)**; $60\text{ connections/tenant}$. | Path: `batch_id` (`UUIDv7`). Header: `Last-Event-ID` for seamless stream resumption. | `200 OK` (Streaming): Real-time `BatchProgressTelemetryEvent` frames until terminal batch state. |
| `GET /api/v1/requisitions/{req_id}/candidates` | `recruiter:read` `hiring_mgr:read` | **Safe / Idempotent**; Cursor-paginated; $240\text{ req/min}$ per user. | Query: `tier_filter`, `min_score`, `sort_by`, `cursor_token`, `page_size` ($\le 100$). | `200 OK`: `PaginatedCandidateMatchListResponse` with composite scores and skill-gap vectors. |
| `POST /api/v1/batches/{batch_id}/dlq-replay` | `recruiter:write` | **Idempotent** via `Idempotency-Key`; $5\text{ req/min}$ per tenant. | `ReplayDeadLetterDocumentsRequest`: Optional filter of `DocumentIDs` in `DEAD_LETTER_QUEUE` state. | `202 Accepted`: `DLQReplayDispatchedResponse` with count of re-queued documents. |

#### Table 1.2.2: Live Interview & Evaluation Analytics Endpoints

| Endpoint URI & Verb | Auth Scope & RBAC | Idempotency & Rate Limit Policy | Conceptual Request Contract | Conceptual Response & Status |
| :--- | :--- | :--- | :--- | :--- |
| `POST /api/v1/requisitions/{req_id}/interviews/invite` | `recruiter:write` `hiring_mgr:write` | **Idempotent** per `(CandidateID, RequisitionID)` pair; $60\text{ req/min}$. | `DispatchInterviewInvitationsRequest`: CandidateIDList, ExpirationHours ($\in [24, 168]$), ModalityPolicy. | `202 Accepted`: `InvitationBatchResultResponse` with provisioned `SessionIDs` and expiration timestamps. |
| `POST /api/v1/interviews/{session_id}/handshake` | `candidate:session_magic_link` | **Idempotent** token exchange; $15\text{ req/min}$ per IP/Candidate. | `CandidateSessionHandshakeRequest`: OneTimeMagicToken, ClientDeviceFingerprint, AudioCapabilities. | `200 OK`: `InterviewBootstrapResponse` with short-lived `WSTicketJWT`, IcebreakerContext, and TurnState. |
| `GET /api/v1/interviews/{session_id}/report` | `recruiter:read` `hiring_mgr:read` | **Safe / Idempotent**; $180\text{ req/min}$ per user. | Path: `session_id` (`UUIDv7`). Query: `include_transcript_ledger` (`Boolean`). | `200 OK`: `CandidateEvaluationReportResponse` with rubric scores, Socratic citations, and Integrity Index. |
| `POST /api/v1/requisitions/{req_id}/comparison-matrix` | `recruiter:read` `hiring_mgr:read` | **Safe / Idempotent**; $120\text{ req/min}$ per user. | `GenerateComparisonMatrixRequest`: CandidateIDList (Cardinality $\in [2, 4]$), NormalizationMode. | `200 OK`: `SideBySideComparisonMatrixResponse` aligning candidates across rubric dimensions and authenticity. |
| `PATCH /api/v1/candidates/{cand_id}/disposition` | `recruiter:write` `hiring_mgr:write` | **Idempotent** via `If-Match` version header; $60\text{ req/min}$. | `OverrideCandidateDispositionRequest`: TargetStage, HumanOverrideJustification, RubricAdjustmentNotes. | `200 OK`: `DispositionAuditReceiptResponse` recording immutable human-in-the-loop decision. |

### 1.3 Standardized Conceptual Data Contracts (REST Envelopes)

#### Table 1.3.1: `InitiateScreeningBatchRequest` & `BatchInitiatedResponse` Contract

| Contract Direction | Field Name | Conceptual Data Type | Validation & Domain Constraints | Architectural Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **Request** | `RequisitionID` | `UUIDv7` | Must reference an `OPEN` requisition owned by `TenantID`. | Binds all uploaded CVs to the target JD rubric. |
| **Request** | `BatchClientReference` | `BoundedString(Max=120)` | Alphanumeric and hyphens; non-empty. | Human-readable batch identifier for recruiter tracking. |
| **Request** | `DocumentManifest` | `List<FileManifestEntry>` | Cardinality $N \in [1, 500]$. | Declares every file prior to binary transfer for pre-allocation. |
| **Request** | `ManifestEntry.ClientFileID` | `UUIDv4` | Unique within the request manifest. | Correlates client file selection with generated upload URIs. |
| **Request** | `ManifestEntry.FileName` | `SanitizedFileNameString` | Max $255\text{ chars}$; stripped of path traversal sequences (`../`). | Stored as metadata only after PII sanitization. |
| **Request** | `ManifestEntry.FileSizeBytes` | `PositiveInteger` | Range $[1024, 15728640]$ ($1\text{ KB}$ to $15\text{ MB}$). | Rejects empty or oversized files before network upload. |
| **Request** | `ManifestEntry.ContentSHA256` | `HexString(Length=64)` | Valid lowercase SHA-256 hex digest. | Enables instant pre-upload deduplication and integrity verification. |
| **Request** | `ManifestEntry.DeclaredMimeType` | `MimeTypeEnum` | Enum: `{APPLICATION_PDF, APPLICATION_VND_DOCX}`. | Enforces strict format whitelist at the API boundary. |
| **Response** | `BatchID` | `UUIDv7` | Time-ordered aggregate root identifier. | Primary tracking key for batch orchestration. |
| **Response** | `UploadInstructions` | `List<PreSignedUploadSlot>` | Contains `DocumentID`, `PreSignedPutURI`, `RequiredHeaders`, `ExpiresAt`. | Offloads heavy binary I/O from FastAPI directly to Object Storage. |
| **Response** | `DeduplicatedSkippedIDs` | `List<ClientFileID>` | Files whose `ContentSHA256` already exists in this requisition. | Saves bandwidth and LLM tokens by skipping identical resumes. |
| **Response** | `ProgressStreamURI` | `RelativeURIString` | Format `/api/v1/batches/{batch_id}/events`. | Endpoint for real-time Server-Sent Events (SSE) progress telemetry. |

#### Table 1.3.2: Standardized RFC 9457 Problem Details Error Contract (`DomainErrorEnvelope`)

All $4\text{xx}$ and $5\text{xx}$ responses across REST controllers and WebSocket handshake endpoints conform to a deterministic Problem Details schema:

| Field Name | Conceptual Data Type | Constraints & Validation Rules | Domain Purpose & Security Guarantee |
| :--- | :--- | :--- | :--- |
| `TypeURI` | `URIReferenceString` | Format `urn:ai-screener:error:{bounded_context}:{error_slug}`. | Machine-readable taxonomy identifier for client error handling. |
| `Title` | `BoundedString(Max=100)` | Canonical human-readable category summary. | High-level summary safe for UI display. |
| `StatusCode` | `HTTPStatusInteger` | Standard HTTP status code ($\in [400, 599]$). | Transport-level status alignment. |
| `Detail` | `BoundedString(Max=500)` | Redacted, user-safe explanation of the invariant violation. | Zero internal database table names, SQL queries, or prompt templates leaked. |
| `InstanceURI` | `URIReferenceString` | Request path combined with `TraceID`. | Correlates client error reports with distributed OpenTelemetry traces. |
| `DomainErrorCode` | `CanonicalErrorCodeEnum` | E.g., `RUBRIC_WEIGHT_SUM_INVALID`, `SESSION_EXPIRED`, `BATCH_QUOTA_EXCEEDED`. | Deterministic switch key for Next.js frontend state recovery. |
| `ValidationViolations` | `List<FieldViolationTuple>` | Optional list of `(FieldPath, RejectedConstraint, ReasonMessage)`. | Populated on `422 Unprocessable Entity` schema failures. |
| `RetryAfterSeconds` | `OptionalPositiveInteger` | Populated on `429 Too Many Requests` or `503 Service Unavailable`. | Governs client-side exponential backoff scheduling. |

---

## 2. Distributed Worker & Batch Orchestration Architecture

### 2.1 Architecture Decision Record: ADR-006 (Transactional Outbox & Two-Tier Queue Topology)

| ADR Field | Specification Details |
| :--- | :--- |
| **Identifier** | **ADR-006: Transactional Outbox Pattern & Priority-Partitioned Distributed Worker Topology** |
| **Status** | Accepted & Enforced |
| **Context** | When a recruiter commits a batch of $500$ CVs, publishing $500$ task messages directly to a message broker inside an HTTP controller introduces a dual-write vulnerability: if the PostgreSQL transaction commits `ScreeningBatch = PROCESSING` but the Redis/RabbitMQ broker connection drops mid-loop, documents become permanently orphaned in a zombie state. Furthermore, a single tenant uploading five $500$-CV batches ($2,500$ CVs) could monopolize all worker processes, starving smaller batches from other tenants ("Noisy Neighbor Problem"). |
| **Decision** | 1. **Transactional Outbox Relay:** `CommitBatchUploadsUseCase` writes the `ScreeningBatch` state transition and $N$ `OutboxTaskRecord` entries inside a **single atomic PostgreSQL transaction**. An asynchronous, lock-free `OutboxRelayDaemon` (`FOR UPDATE SKIP LOCKED`) streams uncommitted outbox records into the Distributed Task Broker in micro-batches of $100$ messages. 2. **Three-Tier Stage-Isolated Worker Pools:** Separate physical worker pools by resource profile: (a) **I/O & CPU Pool (`pool_parse_pii`):** Handles Virus Scanning, Layout/OCR Parsing, and PII Vaulting; (b) **Rate-Limited AI Extraction Pool (`pool_llm_extract`):** Handles LangChain structured extraction and embedding generation under strict token-bucket governance; (c) **Domain Scoring Pool (`pool_hybrid_match`):** Executes vector retrieval and deterministic mathematical scoring. 3. **Weighted Fair Queuing (WFQ) per Tenant:** Task routing applies Deficit Round-Robin (DRR) scheduling across active `TenantID` queues so no single tenant can consume more than $40\%$ of global extraction capacity when multiple tenants have active batches. |
| **Alternatives Considered** | 1. *Direct Controller Broker Publish:* Rejected due to dual-write inconsistency under network partitions. 2. *Monolithic Single-Stage Worker (Parse + LLM + Score in one task):* Rejected because CPU-heavy OCR blocks async event loops waiting on high-latency LLM I/O, and an LLM rate-limit retry would wastefully re-run OCR. |
| **Consequences** | **Positive:** Guarantees at-least-once task delivery with zero orphaned documents; isolates CPU-bound OCR from I/O-bound LLM calls; enforces multi-tenant SLA fairness. **Trade-offs:** Introduces a sub-$150\text{ ms}$ polling/CDC relay delay between batch commit and worker pick-up. |

### 2.2 Mathematical Sizing, Rate-Limit Budgeting & Backpressure Formulation

To guarantee **KPI-SYS-02** ($500\text{ CVs processed within } T_{\text{SLA}} = 180\text{ seconds}$, i.e., $\Theta_{\text{target}} \ge 2.78\text{ CVs/sec}$) without triggering upstream LLM provider HTTP $429$ cascades, worker concurrency and backpressure are governed by three mathematical laws:

#### 1. Little's Law Concurrency Sizing per Stage

For any pipeline stage $s \in \{\text{parse}, \text{extract}, \text{match}\}$ with mean processing latency $W_s$ (in seconds) and required throughput $\lambda_{\text{target}} = \frac{N_{\text{batch}}}{T_{\text{SLA}} - T_{\text{overhead}}} = \frac{500}{165} \approx 3.03\text{ docs/sec}$, the minimum required concurrent active worker slots $L_s$ is given by Little's Law:

$$
L_s = \lceil \lambda_{\text{target}} \cdot W_{s, P_{95}} \cdot (1 + \zeta_{\text{safety}}) \rceil
$$

Where $\zeta_{\text{safety}} = 0.25$ is the headroom buffer for retry jitter:

* **Stage 1–3 (`pool_parse_pii`):** $W_{\text{parse}, P_{95}} \approx 1.8\text{ s} \implies L_{\text{parse}} = \lceil 3.03 \times 1.8 \times 1.25 \rceil = 7\text{ concurrent CPU worker threads}$.
* **Stage 4–5 (`pool_llm_extract`):** $W_{\text{extract}, P_{95}} \approx 6.5\text{ s} \implies L_{\text{extract}} = \lceil 3.03 \times 6.5 \times 1.25 \rceil = 25\text{ concurrent async I/O coroutines}$.
* **Stage 6 (`pool_hybrid_match`):** $W_{\text{match}, P_{95}} \approx 0.35\text{ s} \implies L_{\text{match}} = \lceil 3.03 \times 0.35 \times 1.25 \rceil = 2\text{ concurrent scoring workers}$.

#### 2. Distributed Token-Bucket LLM Rate-Limit Budgeting

Let the upstream LLM provider enforce a global quota of $R_{\text{TPM}}$ Tokens Per Minute and $R_{\text{RPM}}$ Requests Per Minute. Critically, **Live Adaptive Interviews (Phase 4)** must never suffer latency degradation due to background CV batch ingestion. Therefore, global LLM capacity is partitioned via a **Strict Priority Reservation Policy**:

$$
R_{\text{TPM}}^{\text{batch}}(t) = \max\left( R_{\text{TPM}}^{\min}, \, R_{\text{TPM}}^{\text{global}} - \widehat{R}_{\text{TPM}}^{\text{interview}}(t) - R_{\text{TPM}}^{\text{reserve}} \right)
$$

Where:

* $\widehat{R}_{\text{TPM}}^{\text{interview}}(t)$ is the real-time sliding-window token consumption of active WebSocket interview sessions.
* $R_{\text{TPM}}^{\text{reserve}} = 0.20 \cdot R_{\text{TPM}}^{\text{global}}$ is a dedicated burst reserve held exclusively for live interviews.
* If an average CV extraction consumes $\mu_{\text{tok}} \approx 4,200\text{ tokens}$ (input markdown + structured output), the maximum permissible extraction dispatch rate $\lambda_{\text{dispatch}}(t)$ (in docs/sec) across all `pool_llm_extract` workers is dynamically throttled by a Redis-backed Distributed Token Bucket:

$$
\lambda_{\text{dispatch}}(t) = \min\left( \frac{R_{\text{TPM}}^{\text{batch}}(t)}{60 \cdot \mu_{\text{tok}}}, \, \frac{R_{\text{RPM}}^{\text{batch}}(t)}{60} \right)
$$

#### 3. Adaptive Queue Backpressure & Consumer Pause Factor

To prevent memory exhaustion in intermediate queues when downstream stages slow down, each upstream stage monitors the downstream queue depth $Q_{\text{depth}}(t)$ against high-water mark $Q_{\text{HWM}} = 250$ and critical limit $Q_{\text{crit}} = 600$. The worker prefetch delay $\Delta t_{\text{throttle}}(t)$ is computed continuously as:

$$
\Delta t_{\text{throttle}}(t) = \begin{cases} 0 & \text{if } Q_{\text{depth}}(t) \le Q_{\text{HWM}} \\ T_{\text{max\_pause}} \cdot \left( \frac{Q_{\text{depth}}(t) - Q_{\text{HWM}}}{Q_{\text{crit}} - Q_{\text{HWM}}} \right)^2 & \text{if } Q_{\text{HWM}} < Q_{\text{depth}}(t) < Q_{\text{crit}} \\ \text{PAUSE\_CONSUMPTION} & \text{if } Q_{\text{depth}}(t) \ge Q_{\text{crit}} \end{cases}
$$

### 2.3 Batch Lifecycle State Machine & Aggregation Invariants

The `ScreeningBatch` Aggregate Root tracks the macro-state of all $N_{\text{total}}$ documents. Let $n_{\text{succ}}$, $n_{\text{fail}}$, $n_{\text{quar}}$, and $n_{\text{dup}}$ denote the counts of successfully scored, dead-letter failed, security-quarantined, and duplicate-skipped documents respectively, and let $n_{\text{terminal}} = n_{\text{succ}} + n_{\text{fail}} + n_{\text{quar}} + n_{\text{dup}}$.

```
stateDiagram-v2
    [*] --> INITIATED_AWAITING_UPLOAD : POST /batches (Manifest Validated)
    INITIATED_AWAITING_UPLOAD --> UPLOAD_EXPIRED_ABANDONED : TTL Elapsed (60m) Without Commit
    INITIATED_AWAITING_UPLOAD --> QUEUED_IN_OUTBOX : POST /batches/{id}/commit (Atomic Outbox Write)
    
    QUEUED_IN_OUTBOX --> PROCESSING_ACTIVE : First Document Claimed by Worker
    
    state PROCESSING_ACTIVE {
        [*] --> NORMAL_THROUGHPUT
        NORMAL_THROUGHPUT --> THROTTLED_BACKPRESSURE : Downstream Queue > HWM or LLM 429 Burst
        THROTTLED_BACKPRESSURE --> NORMAL_THROUGHPUT : Queue Drained & Token Bucket Replenished
    }

    PROCESSING_ACTIVE --> COMPLETED_SUCCESS : n_terminal == N_total AND (n_fail + n_quar) == 0
    PROCESSING_ACTIVE --> COMPLETED_PARTIAL_FAILURE : n_terminal == N_total AND n_succ > 0 AND (n_fail + n_quar) > 0
    PROCESSING_ACTIVE --> FAILED_TOTAL_OUTAGE : n_terminal == N_total AND n_succ == 0
    
    COMPLETED_PARTIAL_FAILURE --> REPLAYING_DLQ : POST /batches/{id}/dlq-replay
    FAILED_TOTAL_OUTAGE --> REPLAYING_DLQ : POST /batches/{id}/dlq-replay
    REPLAYING_DLQ --> PROCESSING_ACTIVE : Failed Documents Re-Queued
```

### 2.4 Real-Time Batch Progress Telemetry & Server-Sent Events (SSE) Topology

Rather than forcing the Next.js Recruiter Dashboard to poll PostgreSQL every second for $500$ individual document statuses, progress updates use a **Coalesced Redis Pub/Sub $\rightarrow$ Server-Sent Events (SSE)** architecture:

1. **Atomic Counter Mutation:** Whenever a worker completes a stage transition for `DocumentID`, it executes an atomic pipeline updating the PostgreSQL document row and incrementing Redis hash counters `batch:{batch_id}:metrics` (`HINCBY stage_parsed 1`, `HINCBY stage_extracted 1`, `HINCBY stage_scored 1`).
2. **Windowed Event Coalescing ($250\text{ ms}$ Debounce):** To prevent flooding the browser with $3,000$ individual SSE frames during a $500$-CV batch ($6\text{ stages} \times 500\text{ docs}$), the SSE Gateway coalesces state updates over a $250\text{ ms}$ tumbling window ($\max 4\text{ UI updates/second}$), emitting a single `BatchProgressTelemetryEvent` snapshot containing stage histograms, throughput velocity ($\text{docs/sec}$), estimated time remaining ($\text{ETA}$), and any newly completed candidate match cards.

---

## 3. WebSocket Real-Time Communication Protocol

### 3.1 Architecture Decision Record: ADR-007 (Full-Duplex WebSocket Protocol & Barge-In Cancellation)

| ADR Field | Specification Details |
| :--- | :--- |
| **Identifier** | **ADR-007: Full-Duplex Stateful WebSocket Protocol with Monotonic Sequence Framing & Barge-In Cancellation** |
| **Status** | Accepted & Enforced |
| **Context** | Live technical interviews require natural, low-latency conversational turn-taking ($P_{95} \le 1,850\text{ ms}$). Candidates may speak over or interrupt the AI interviewer mid-sentence to clarify a point ("barge-in"), experience transient Wi-Fi drops, or switch between voice and text modalities (e.g., speaking architectural explanations while pasting a time-complexity expression in text). Standard HTTP chunked streaming is half-duplex and cannot coordinate real-time audio interruption, client-side integrity telemetry, and deterministic LangGraph state recovery. |
| **Decision** | 1. **Single Multiplexed Full-Duplex WebSocket Connection:** Multiplex control frames, incremental STT transcripts, TTS audio chunks, integrity telemetry beacons, and state transitions over a single `/ws/v1/interviews/{session_id}` connection using a strict envelope protocol with monotonic `ClientSeq` and `ServerSeq` counters. 2. **Two-Phase Cryptographic Ticket Handshake:** Prohibit passing long-lived tokens in WebSocket query strings; exchange the invitation magic token via `POST /handshake` for a single-use, $60\text{-second}$ TTL, IP-bound `WSTicketJWT` redeemed during the WebSocket `Upgrade` handshake. 3. **Preemptive Barge-In Cancellation Token Propagation:** Maintain a server-side `TurnGenerationCancellationScope` per active turn. If the Voice Activity Detector (VAD) confirms sustained candidate speech ($> 280\text{ ms}$ voiced energy) while the AI is still streaming TTS audio, the gateway immediately aborts the active TTS stream, sends a `SERVER_TTS_FLUSH_INTERRUPT` signal to clear the client audio buffer, and truncates the assistant's partial utterance in the LangGraph transcript ledger to reflect only what the candidate actually heard. |
| **Alternatives Considered** | 1. *WebRTC Media Servers (SFU) + Separate REST Control Plane:* Rejected for Phase 1–8 baseline due to operational complexity of TURN/STUN media clusters when WebSocket binary Opus/PCM framing achieves $< 120\text{ ms}$ transport latency; however, `SpeechStreamPort` remains media-transport agnostic so WebRTC can be added as an alternative adapter. 2. *Half-Duplex Turn Locking (Blocking Candidate Mic while AI Speaks):* Rejected because it creates an unnatural, walkie-talkie user experience and frustrates candidates when they want to interject. |
| **Consequences** | **Positive:** Sub-second conversational responsiveness; natural human-like interruption handling; seamless automatic reconnection without losing a single LangGraph turn. **Trade-offs:** Requires stateful per-connection asyncio task management and distributed Redis session lease locks to prevent split-brain connections. |

### 3.2 Connection Lifecycle, Lease Locking & Network Drop Recovery

```
sequenceDiagram
    autonumber
    actor Candidate as Candidate Browser (Next.js)
    participant REST as Presentation: HandshakeController
    participant WSGate as Presentation: WebSocketInterviewGateway
    participant LeaseMgr as Infrastructure: RedisSessionLeaseAdapter
    participant UseCase as Application: ProcessInterviewTurnUseCase
    participant Checkpointer as Infrastructure: LangGraphPostgresCheckpointer
    participant SpeechACL as Infrastructure: StreamingSpeechAdapter

    Candidate->>REST: POST /api/v1/interviews/{session_id}/handshake (MagicToken)
    REST->>REST: Verify Token, Session Window & Device Fingerprint
    REST-->>Candidate: 200 OK (OneTime WSTicketJWT, TTL = 60s, LastCommittedSeq)

    Candidate->>WSGate: WSS Upgrade /ws/v1/interviews/{session_id} (Auth: WSTicketJWT)
    WSGate->>LeaseMgr: AcquireExclusiveLease(SessionID, WorkerNodeID, TTL = 30s)
    alt Concurrent Connection Already Active on Another Node
        LeaseMgr-->>WSGate: ExistingLeaseFound(OldNodeID)
        WSGate->>LeaseMgr: Publish ForceEvictOldSocket(SessionID)
        LeaseMgr-->>WSGate: LeaseTransferredToNewSocket
    end
    WSGate->>Checkpointer: LoadLatestGraphCheckpoint(SessionID)
    Checkpointer-->>WSGate: Return RestoredInterviewState (TurnIndex, ActiveQuestion, AudioOffset)
    WSGate-->>Candidate: Frame: SERVER_SESSION_READY (RestoredState, ServerSeq = k)

    loop Every 10 Seconds (Keepalive & Lease Renewal)
        Candidate->>WSGate: Frame: CLIENT_HEARTBEAT_PING (ClientTimestamp, LastReceivedServerSeq)
        WSGate->>LeaseMgr: RenewLeaseTTL(SessionID, +30s)
        WSGate-->>Candidate: Frame: SERVER_HEARTBEAT_PONG (ServerTimestamp, LatencyRTT)
    end

    Note over Candidate,WSGate: Transient Network Drop Occurs Mid-Interview
    Candidate--xWSGate: TCP Socket Disconnected
    WSGate->>SpeechACL: CancelActiveAudioStreams(SessionID)
    WSGate->>LeaseMgr: TransitionLeaseToGracePeriod(SessionID, GraceTTL = 180s)
    Note over WSGate,Checkpointer: Session Paused at Last Committed Checkpoint (Turn Integrity Preserved)

    Candidate->>WSGate: WSS Reconnect with ResumeToken + LastReceivedServerSeq
    WSGate->>Checkpointer: FetchCheckpointAndReplayDelta(SessionID, AfterSeq = LastReceivedServerSeq)
    WSGate-->>Candidate: Frame: SERVER_SESSION_RESUMED (ReplayedFrames[], CurrentActivePrompt)
```

### 3.3 Bi-Directional WebSocket Event Contract Matrix

Every WebSocket message (JSON text frame for control/events; binary frame prefixed with a $16\text{-byte}$ header for raw Opus/PCM audio chunks) conforms to a strict envelope containing `EventID` (`UUIDv7`), `EventType`, `SessionID`, `TurnIndex`, `SequenceNumber` (monotonically increasing per direction), `TimestampUTC`, and `Payload`.

#### Table 3.3.1: Client $\rightarrow$ Server Inbound WebSocket Events (`CandidateToGateway`)

| Event Name | Frame Type | Conceptual Payload Schema | Trigger Condition & Cadence | Server-Side Validation & Error Handling |
| :--- | :--- | :--- | :--- | :--- |
| `CLIENT_SESSION_INIT` | JSON Control | `ClientResumeSeq`, `AudioCodecConfig` (`OPUS_48KHZ` or `PCM_16KHZ`), `ViewportDimensions`. | Sent immediately upon WebSocket `onopen` completion. | Verifies codec support; if invalid, emits `SERVER_ERROR` (`UNSUPPORTED_AUDIO_CODEC`). |
| `CLIENT_AUDIO_CHUNK` | Binary Stream | Header: `TurnIndex` ($2\text{B}$), `ChunkSeq` ($4\text{B}$), `RMS Energy` ($2\text{B}$). Body: Encoded Audio Bytes ($20\text{--}40\text{ ms}$ frame). | Streamed continuously every $20\text{--}40\text{ ms}$ while candidate microphone is active. | Dropped if session state is `COMPLETED`; triggers backpressure warning if rate $> 60\text{ chunks/s}$. |
| `CLIENT_BARGE_IN_SIGNAL` | JSON Control | `InterruptedTurnIndex`, `InterruptedAudioPlaybackMs`, `ClientVADConfidence` ($\in [0,1]$). | Emitted when client-side VAD detects candidate speech while AI TTS is playing. | Aborts active TTS stream; logs exact `InterruptedAudioPlaybackMs` in turn history. |
| `CLIENT_TEXT_SUPPLEMENT` | JSON Control | `TurnIndex`, `SupplementalTextContent` (Max $4,000\text{ chars}$), `InputOrigin` (`TYPED` vs. `PASTED`). | Candidate submits a written architectural formula, complexity expression, or text response. | Sanitized against prompt-injection delimiters before merging with voice transcript. |
| `CLIENT_TURN_COMMIT` | JSON Control | `TurnIndex`, `FinalClientAudioSeq`, `ManualEndOfTurnFlag` (`Boolean`). | Candidate clicks *"Done Speaking"* button or client VAD confirms end-of-turn silence. | Flushes STT buffer, locks turn input, and invokes `ProcessInterviewTurnUseCase`. |
| `CLIENT_INTEGRITY_TELEMETRY` | JSON Control | `TurnIndex`, `TelemetrySignals`: `VisibilityState`, `PasteCharCount`, `PasteDeltaMs`, `FocusLossDurationMs`. | Emitted immediately on anomaly detection (tab blur, paste burst) or batched per turn. | Validated and appended to `IntegrityTelemetryWindow` for Phase 4 authenticity scoring. |
| `CLIENT_HEARTBEAT_PING` | JSON Control | `ClientMonotonicTimestampMs`, `LastProcessedServerSeq`. | Every $10,000\text{ ms}$ during active connection. | Updates RTT telemetry and renews Redis session lock lease; timeout at $30,000\text{ ms}$. |

#### Table 3.3.2: Server $\rightarrow$ Client Outbound WebSocket Events (`GatewayToCandidate`)

| Event Name | Frame Type | Conceptual Payload Schema | Trigger Condition & Cadence | Client-Side Action & Error Recovery |
| :--- | :--- | :--- | :--- | :--- |
| `SERVER_SESSION_READY` | JSON Control | `SessionStatus`, `CurrentTurnIndex`, `TotalPlannedCompetencies`, `ActiveQuestionPrompt`, `ServerSeq`. | Emitted upon initial authentication or successful reconnection recovery. | Hydrates Next.js interview state machine and synchronizes sequence counters. |
| `SERVER_STT_PARTIAL` | JSON Control | `TurnIndex`, `PartialTranscriptText`, `StabilityConfidence` ($\in [0, 1]$), `WordTimestamps`. | Streamed every $150\text{--}300\text{ ms}$ as the streaming STT engine decodes candidate speech. | Renders live, low-opacity real-time captions in the candidate portal UI. |
| `SERVER_STT_COMMITTED` | JSON Control | `TurnIndex`, `FinalizedTranscriptSegment`, `UtteranceDurationMs`, `SpeechCadenceWPM`. | Emitted when STT finalizes a sentence or upon end-of-turn silence commit. | Locks candidate transcript bubble in UI and transitions UI state to `THINKING_EVALUATING`. |
| `SERVER_COGNITIVE_STATE` | JSON Control | `TurnIndex`, `ExecutionStage` (`EVALUATING_RESPONSE`, `PLANNING_PROBE`, `SYNTHESIZING_SPEECH`). | Emitted at LangGraph node transitions inside `LangGraphInterviewOrchestratorACL`. | Updates subtle visual thinking indicators so the candidate perceives active responsiveness. |
| `SERVER_QUESTION_TEXT_DELTA` | JSON Control | `TurnIndex`, `QuestionId`, `TextTokenDelta`, `TargetCompetencyLabel`, `IsSocraticFollowUp`. | Streamed token-by-token as `QuestionGeneratorNode` or `SocraticFollowUpNode` emits text. | Incrementally renders the interviewer's question text synchronized with audio playback. |
| `SERVER_TTS_AUDIO_CHUNK` | Binary Stream | Header: `TurnIndex` ($2\text{B}$), `AudioSeq` ($4\text{B}$), `DurationMs` ($2\text{B}$). Body: Opus/PCM Audio Frame. | Streamed as sentence-level text chunks are synthesized by the streaming TTS adapter. | Queued into the Web Audio API jitter ring buffer for gapless voice playback. |
| `SERVER_TTS_FLUSH_INTERRUPT` | JSON Control | `InterruptedTurnIndex`, `Reason` (`CANDIDATE_BARGE_IN`), `TruncatedTextPosition`. | Emitted immediately when server confirms candidate barge-in speech. | Instantly halts Web Audio playback, flushes queued audio buffers, and switches UI to `LISTENING`. |
| `SERVER_TURN_READY_FOR_INPUT` | JSON Control | `TurnIndex`, `MaxTurnDurationSeconds`, `ExpectedResponseModality`. | Emitted when AI finishes speaking its question and opens the floor to the candidate. | Starts candidate turn timer and activates microphone streaming indicator. |
| `SERVER_INTERVIEW_COMPLETED` | JSON Control | `SessionID`, `CompletionReason`, `TotalTurnsCompleted`, `ClosingMessageText`, `RedirectURI`. | Emitted when LangGraph transitions to `ScoringVerificationNode` and reaches `[END]`. | Gracefully closes microphone streams and transitions UI to the completion confirmation screen. |
| `SERVER_ERROR_NOTIFICATION` | JSON Control | `DomainErrorEnvelope`, `IsRecoverable` (`Boolean`), `SuggestedClientAction`. | Emitted on validation error, transient STT/LLM timeout, or rate-limit violation. | If `IsRecoverable = TRUE`, triggers soft retry; if `FALSE`, initiates graceful reconnect or exit. |

### 3.4 Low-Latency Voice (STT/TTS), VAD Silence Detection & Barge-In Synchronization Mathematics

Achieving **KPI-SYS-01** ($P_{50} \le 1,100\text{ ms}$, $P_{95} \le 1,850\text{ ms}$ turn latency) in a technical interview requires avoiding premature cutoffs when a candidate pauses to think about an architectural trade-off, while responding rapidly once they finish speaking.

#### 1. Context-Aware Adaptive End-of-Turn Silence Threshold ($\tau_{\text{EOT}}$)

Standard voice assistants use a rigid $500\text{ ms}$ silence cutoff, which severes technical explanations mid-thought when an engineer pauses to recall a complexity bound or system component. Our `StreamingSpeechAdapter` implements a **Syntactic & Acoustic Adaptive End-of-Turn Threshold** $\tau_{\text{EOT}}(t)$ (in milliseconds):

$$
\tau_{\text{EOT}}(t) = \tau_{\text{base}} + \Delta\tau_{\text{syntactic}}\left(\mathcal{W}_{\text{tail}}\right) + \Delta\tau_{\text{hesitation}}\left(f_0, E_{\text{RMS}}\right)
$$

Where:

* $\tau_{\text{base}} = 750\text{ ms}$ is the baseline silence threshold after a syntactically complete clause.
* $\Delta\tau_{\text{syntactic}}\left(\mathcal{W}_{\text{tail}}\right) \in \{0, 650\}\text{ ms}$ inspects the trailing $3$ tokens $\mathcal{W}_{\text{tail}}$ of `SERVER_STT_PARTIAL`. If the trailing tokens end in a conjunction, preposition, or discourse marker (*"because"*, *"such as"*, *"whereas"*, *"and then"*, *"for example"*), $\Delta\tau_{\text{syntactic}} = +650\text{ ms}$ to allow the candidate to complete their clause.
* $\Delta\tau_{\text{hesitation}}\left(f_0, E_{\text{RMS}}\right) = +450\text{ ms}$ is added if the last voiced frame exhibited a flat/rising fundamental pitch contour ($f_0$) characteristic of mid-thought vocalized pauses (*"um"*, *"uh"*, *"let's see"*).
* Consequently, complete sentences commit within $750\text{ ms}$, while mid-clause thinking pauses gracefully wait up to $1,850\text{ ms}$ before auto-committing (and candidates can always press `Spacebar` / *"Done Speaking"* for $0\text{ ms}$ instant commit).

#### 2. Speculative Parallel Prefetching & Sentence-Boundary TTS Pipelining

To minimize perceived turn latency $L_{\text{turn}}$, the `LangGraphInterviewOrchestratorACL` and `StreamingSpeechAdapter` execute a **Speculative Sentence-Pipelined Streaming Architecture**:

1. **Parallel Evaluation & Context Prefetch:** As soon as VAD silence exceeds $0.6 \cdot \tau_{\text{EOT}}(t)$ ($450\text{ ms}$), the gateway snapshots the current transcript and pre-warms the LangGraph checkpoint state in memory. If the candidate resumes speaking before $\tau_{\text{EOT}}(t)$, the speculative warm-up is discarded at zero state mutation cost.
2. **Clause-Level TTS Synthesis:** The `QuestionGeneratorNode` and `SocraticFollowUpNode` do not wait for the entire $60$-word question to finish generating before invoking Text-to-Speech. Instead, an outbound token stream accumulator buffers LLM output tokens until the first prosodic clause boundary (punctuation $\in \{., ?, !, :, ;\}$ with token count $\ge 8$) and immediately pipes that clause into `SpeechStreamPort.SynthesizeClauseStream()`.
3. **End-to-End Turn Latency Budget Breakdown:**

$$
L_{\text{turn}} = \underbrace{t_{\text{STT\_flush}}}_{\le 120\text{ ms}} + \underbrace{t_{\text{EvalNode}}}_{\le 480\text{ ms}} + \underbrace{t_{\text{GenNode\_first\_clause}}}_{\le 320\text{ ms}} + \underbrace{t_{\text{TTS\_first\_frame}}}_{\le 150\text{ ms}} + \underbrace{t_{\text{WS\_jitter\_buffer}}}_{\le 60\text{ ms}} \approx 1,130\text{ ms}
$$

```
flowchart LR
    subgraph CandidateClient["Candidate Browser (Web Audio & VAD)"]
        Mic["Microphone Capture\n(20ms Opus Frames)"]
        ClientVAD["Client VAD & Barge-In Detector"]
        JitterBuf["Adaptive Audio Ring Buffer\n(60ms Jitter Cushion)"]
    end

    subgraph FastAPIGateway["Presentation: WebSocketInterviewGateway"]
        Demux["Frame Demultiplexer &\nSeq Validator"]
        CancelScope["Turn Cancellation Token Manager"]
        Mux["Outbound Frame Multiplexer"]
    end

    subgraph SpeechInfra["Infrastructure: StreamingSpeechAdapter"]
        StreamSTT["Streaming STT Engine +\nSyntactic EOT Detector"]
        ClauseSplit["Prosodic Clause Accumulator\n(Min 8 Tokens + Punctuation)"]
        StreamTTS["Streaming Low-Latency TTS Engine"]
    end

    subgraph CognitiveGraph["Infrastructure: LangGraph Orchestrator ACL"]
        EvalNode["EvaluationNode\n(Structured Rubric Grading)"]
        RouteEdge{"Conditional Router"}
        GenNode["QuestionGenerator /\nSocraticFollowUp Node"]
    end

    Mic -->|"CLIENT_AUDIO_CHUNK"| Demux
    ClientVAD -->|"CLIENT_BARGE_IN_SIGNAL"| CancelScope
    Demux --> StreamSTT
    StreamSTT -->|"SERVER_STT_PARTIAL"| Mux
    StreamSTT -->|"EOT Silence Committed"| EvalNode
    EvalNode --> RouteEdge
    RouteEdge --> GenNode
    GenNode -->|"Token Stream"| ClauseSplit
    GenNode -->|"SERVER_QUESTION_TEXT_DELTA"| Mux
    ClauseSplit -->|"First Complete Clause"| StreamTTS
    StreamTTS -->|"SERVER_TTS_AUDIO_CHUNK"| Mux
    CancelScope -.->|"Preempt & Abort"| StreamTTS
    CancelScope -.->|"SERVER_TTS_FLUSH_INTERRUPT"| Mux
    Mux --> JitterBuf
```

### 3.5 Outbound Application Port Specification: `StreamingSpeechPort` & `BatchProgressPublisherPort`

| Port Interface Name | Method Signature (Conceptual) | Preconditions & Input Contract | Postconditions & Output Contract | Failure Modes & Fallback Policy |
| :--- | :--- | :--- | :--- | :--- |
| **`StreamingSpeechPort`** | `OpenDuplexTranscriptionStream(SessionID, AudioCodecConfig, AdaptiveEOTPolicy) -> AsyncTranscriptEventIterator` | Active `InterviewSession` in `IN_PROGRESS` state; valid audio sample rate ($16\text{ kHz}$ or $48\text{ kHz}$). | Yields immutable `PartialTranscriptDTO` and `FinalizedUtteranceDTO` events with acoustic cadence metadata. | `STTProviderStreamDroppedError` $\rightarrow$ Automatically reconnects to secondary STT provider and seamlessly switches UI to text-fallback prompt if both fail. |
| **`StreamingSpeechPort`** | `SynthesizeProsodicStream(SessionID, TurnIndex, ClauseTextStream, CancellationToken) -> AsyncAudioFrameIterator` | Non-empty clause stream from cognitive graph; active `CancellationToken` not triggered. | Yields $20\text{ ms}$ binary audio frames aligned with word-level character offsets for accurate barge-in truncation. | `TTSProviderTimeoutError` $\rightarrow$ Suppresses voice audio for current turn while preserving instant `SERVER_QUESTION_TEXT_DELTA` text delivery. |
| **`BatchProgressPublisherPort`** | `PublishCoalescedBatchTelemetry(TenantID, BatchID, BatchStageMetricsSnapshot) -> PublishAckReceipt` | Called by worker after atomic stage state transition; metrics satisfy $\sum n_i \le N_{\text{total}}$. | Coalesces updates over $250\text{ ms}$ window and pushes `BatchProgressTelemetryEvent` to all subscribed SSE clients. | `PubSubBrokerUnavailableError` $\rightarrow$ Non-fatal; logs warning and relies on client automatic SSE reconnect with `Last-Event-ID` PostgreSQL state hydration. |