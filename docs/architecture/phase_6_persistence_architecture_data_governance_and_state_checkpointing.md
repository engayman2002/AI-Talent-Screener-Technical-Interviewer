# AI Talent Screener & Technical Interviewer

## Phase 6: Persistence Architecture, Data Governance & State Checkpointing

* **Document ID:** `ARCH-SPEC-PHASE-06`

* **Architectural Paradigm:** Clean Architecture (Persistence Adapter Isolation), Tactical DDD Aggregate Persistence, Cryptographic PII Separation, Distributed Stateful Graph Checkpointing

* **Implementation Policy:** Strict Zero-Code Conceptual & Mathematical Specification

* **Bounded Contexts Covered:** All Contexts (*Recruitment Management*, *Document Intelligence*, *Live Interview*, *Evaluation & Analytics*, and *Identity & Privacy Vault*)

## 1. Relational Persistence Blueprint (PostgreSQL)

### 1.1 Clean Architecture Persistence Governance & Aggregate Mapping

In strict compliance with Robert C. Martin's **Clean Architecture** and **Tactical Domain-Driven Design**, the PostgreSQL relational database is treated strictly as an external **Infrastructure Detail**.

1. **Zero ORM Leakage Invariant:** Domain Entities, Aggregate Roots, and Value Objects in the **Domain Layer** contain zero persistence annotations, foreign-key relational proxies, or lazy-loading session hooks.

2. **Data Mapper & Aggregate Hydration Pattern:** Concrete repository adapters in the **Infrastructure Layer** (e.g., `PostgresJobRequisitionRepository`, `PostgresInterviewSessionRepository`) implement the abstract Repository Ports defined in the Domain/Application layers. Each adapter uses an explicit **Data Mapper ACL** to translate between internal relational table rows (Persistence Models) and rich Domain Aggregates within an atomic **Unit of Work (`UnitOfWorkPort`)**.

3. **Optimistic Concurrency Control (OCC):** Every Aggregate Root table enforces an integer `aggregate_version` column. Every state mutation executes a compare-and-swap update conditioned on $(\text{aggregate\_id} = \text{id} \land \text{aggregate\_version} = v_{\text{expected}})$, atomically incrementing the version to $v_{\text{expected}} + 1$. If zero rows match, the repository raises a `ConcurrencyConflictDomainError`.

4. **Defense-in-Depth Multi-Tenant Isolation:** Every operational table includes a mandatory `tenant_id` (`UUIDv7`) column participating in all composite primary/unique indexes and foreign keys. At the start of every database transaction, the `UnitOfWorkPort` adapter binds the authenticated tenant context to the PostgreSQL session transaction (`app.current_tenant_id`), enforced by mandatory **Row-Level Security (RLS)** policies so a query lacking or mismatching the tenant context can never read or mutate cross-tenant rows.

### 1.2 Architecture Decision Record: ADR-008 (Cryptographic PII Vault Separation & Crypto-Shredding)

| ADR Field | Specification Details | 
| ----- | ----- | 
| **Identifier** | **ADR-008: Physical Schema Isolation of Candidate PII Vault & Per-Candidate Envelope Crypto-Shredding** | 
| **Status** | Accepted & Enforced | 
| **Context** | Enterprise recruiting platforms are subject to strict data privacy regulations (GDPR Article 17 "Right to Erasure", CCPA) alongside AI compliance mandates requiring immutable audit logs of hiring decisions and fairness metrics. Storing candidate Personally Identifiable Information (Full Name, Email, Phone, Raw Unredacted Resume) directly inside operational candidate, transcript, or LangGraph checkpoint tables makes it impossible to purge a candidate's identity upon request without destroying historical hiring analytics and breaking referential integrity across completed requisitions. | 
| **Decision** | 1\. **Dual-Schema Physical Separation:** Partition the PostgreSQL database into two strictly isolated schemas with separate DB role permissions: `identity_vault` (accessible only by the `PIIRedactionVaultAdapter` and authorized Recruiter Identity Resolution queries) and `core_screening` (accessible by AI extraction workers, matching services, and the LangGraph interview engine).  2\. **Surrogate Tokenization:** All tables in `core_screening` reference candidates exclusively via an opaque, unguessable `candidate_id` (`UUIDv7`).  3\. **Per-Candidate Envelope Encryption & Crypto-Shredding:** Inside `identity_vault.candidate_pii_records`, each candidate's PII payload and raw CV object-storage decryption key are encrypted using a unique per-candidate Data Encryption Key ($\text{DEK}_c$, AES-256-GCM), which is itself wrapped by the tenant's KMS Key Encryption Key ($\text{KEK}_{\text{tenant}}$). When a candidate exercises their Right to Erasure or retention expires, the system permanently zeroes and deletes $\text{DEK}_c$ and the encrypted blob (`crypto_shredded_at = NOW()`), rendering all historical PII mathematically unrecoverable in $O(1)$ time while preserving anonymized rubric scores and system telemetry in `core_screening`. | 
| **Alternatives Considered** | 1\. *Hard Cascading `DELETE` Across All Tables:* Rejected because deleting candidate rows cascades into `match_evaluations`, `interview_sessions`, and `screening_batches`, corrupting historical pass-through KPIs and batch totals.  2\. *In-Place Column Masking (`UPDATE SET name = 'REDACTED'`):* Rejected because historical backups, WAL logs, and object storage buckets still retain the plaintext PII unless encrypted with a destructible per-candidate DEK. | 
| **Consequences** | **Positive:** Guarantees provable zero-PII exposure to LLM/LangGraph pipelines; enables instantaneous GDPR/CCPA cryptographic erasure without breaking relational foreign keys or analytical scorecards.  **Trade-offs:** Requires a batch identity-hydration join at the Presentation/Application boundary when rendering candidate names on the Recruiter Dashboard. | 

### 1.3 Enterprise Relational Entity-Relationship Diagram (`erDiagram`)

```
erDiagram
    TENANTS ||--o{ JOB_REQUISITIONS : "owns"
    JOB_REQUISITIONS ||--|{ COMPETENCY_RUBRIC_VERSIONS : "governed_by"
    JOB_REQUISITIONS ||--o{ SCREENING_BATCHES : "ingests_via"
    JOB_REQUISITIONS ||--o{ MATCH_EVALUATIONS : "evaluates_for"
    JOB_REQUISITIONS ||--o{ INTERVIEW_SESSIONS : "conducts_for"

    SCREENING_BATCHES ||--|{ BATCH_DOCUMENT_TASKS : "contains"
    BATCH_DOCUMENT_TASKS ||--o| CANDIDATE_PROFILES : "produces"

    CANDIDATE_PII_VAULT ||--|| CANDIDATE_PROFILES : "anonymizes_into"
    CANDIDATE_PROFILES ||--|{ CANDIDATE_WORK_HISTORY : "has_roles"
    CANDIDATE_PROFILES ||--|{ CANDIDATE_SKILL_EVIDENCE : "demonstrates"
    CANDIDATE_PROFILES ||--|{ CANDIDATE_EPISTEMIC_GAPS : "flags_gaps"
    CANDIDATE_PROFILES ||--o{ MATCH_EVALUATIONS : "scored_in"
    CANDIDATE_PROFILES ||--o{ INTERVIEW_SESSIONS : "participates_in"

    INTERVIEW_SESSIONS ||--|{ INTERVIEW_TURN_LEDGER : "records_turns"
    INTERVIEW_SESSIONS ||--|{ LANGGRAPH_STATE_CHECKPOINTS : "persists_state"
    LANGGRAPH_STATE_CHECKPOINTS ||--o{ LANGGRAPH_CHECKPOINT_WRITES : "buffers_channel_writes"
    INTERVIEW_SESSIONS ||--o| CANDIDATE_EVALUATION_REPORTS : "synthesizes"

    CANDIDATE_EVALUATION_REPORTS ||--|{ REPORT_DIMENSION_SCORES : "breaks_down_into"
    CANDIDATE_EVALUATION_REPORTS ||--o{ REPORT_INTEGRITY_FLAGS : "audits_anomalies"

    TENANTS ||--o{ TRANSACTIONAL_OUTBOX_EVENTS : "publishes"

    TENANTS {
        uuidv7 tenant_id PK
        string organization_slug UK
        string kms_kek_arn
        enum subscription_tier
        timestamp created_at
    }

    JOB_REQUISITIONS {
        uuidv7 requisition_id PK
        uuidv7 tenant_id FK
        uuidv7 active_rubric_version_id FK
        string title
        enum seniority_band
        enum requisition_status
        int aggregate_version
        timestamp created_at
    }

    COMPETENCY_RUBRIC_VERSIONS {
        uuidv7 rubric_version_id PK
        uuidv7 requisition_id FK
        uuidv7 tenant_id FK
        int version_number
        jsonb hard_skill_gatekeepers
        jsonb weighted_dimensions
        decimal auto_invite_threshold
        decimal review_threshold
        timestamp locked_at
    }

    SCREENING_BATCHES {
        uuidv7 batch_id PK
        uuidv7 requisition_id FK
        uuidv7 tenant_id FK
        enum batch_status
        int total_documents_count
        int completed_scored_count
        int failed_dlq_count
        int quarantined_count
        int aggregate_version
        timestamp created_at
    }

    BATCH_DOCUMENT_TASKS {
        uuidv7 document_task_id PK
        uuidv7 batch_id FK
        uuidv7 tenant_id FK
        string content_sha256_hash
        string idempotency_key UK
        enum processing_stage
        int retry_attempt_count
        string quarantine_blob_uri
        timestamp updated_at
    }

    CANDIDATE_PII_VAULT {
        uuidv7 candidate_id PK
        uuidv7 tenant_id FK
        bytes encrypted_pii_payload
        bytes wrapped_candidate_dek
        string blind_email_hmac_index
        boolean is_crypto_shredded
        timestamp retention_expires_at
        timestamp crypto_shredded_at
    }

    CANDIDATE_PROFILES {
        uuidv7 candidate_id PK
        uuidv7 tenant_id FK
        uuidv7 source_document_task_id FK
        int total_verified_exp_months
        enum inferred_seniority_tier
        int vector_sync_version
        enum vector_sync_status
        int aggregate_version
        timestamp extracted_at
    }

    CANDIDATE_WORK_HISTORY {
        uuidv7 work_entry_id PK
        uuidv7 candidate_id FK
        uuidv7 tenant_id FK
        string canonical_role_title
        int start_year_month
        int end_year_month
        int duration_months
        int recency_delta_months
        enum employment_type
    }

    CANDIDATE_SKILL_EVIDENCE {
        uuidv7 skill_evidence_id PK
        uuidv7 candidate_id FK
        uuidv7 tenant_id FK
        string canonical_skill_id
        enum skill_category
        int claimed_proficiency_depth
        int applied_duration_months
        string verbatim_evidence_quote
    }

    CANDIDATE_EPISTEMIC_GAPS {
        uuidv7 gap_signal_id PK
        uuidv7 candidate_id FK
        uuidv7 tenant_id FK
        string target_competency_slug
        string suspect_claim_summary
        decimal probe_priority_weight
    }

    MATCH_EVALUATIONS {
        uuidv7 match_evaluation_id PK
        uuidv7 requisition_id FK
        uuidv7 candidate_id FK
        uuidv7 rubric_version_id FK
        uuidv7 tenant_id FK
        decimal composite_match_score
        decimal gatekeeper_multiplier
        decimal seniority_calibration_factor
        enum assigned_tier
        jsonb competency_breakdown_map
        jsonb epistemic_skill_gap_vector
        timestamp evaluated_at
    }

    INTERVIEW_SESSIONS {
        uuidv7 session_id PK
        uuidv7 requisition_id FK
        uuidv7 candidate_id FK
        uuidv7 rubric_version_id FK
        uuidv7 tenant_id FK
        enum session_status
        int current_turn_index
        bigint active_fencing_token
        string latest_checkpoint_id
        decimal rolling_integrity_index
        int aggregate_version
        timestamp expires_at
        timestamp started_at
        timestamp completed_at
    }

    INTERVIEW_TURN_LEDGER {
        uuidv7 turn_id PK
        uuidv7 session_id FK
        uuidv7 tenant_id FK
        int turn_index
        uuidv7 target_dimension_id
        int question_difficulty_level
        boolean is_socratic_followup
        text interviewer_prompt_text
        text candidate_response_text
        boolean was_barge_in_interrupted
        decimal turn_composite_score
        jsonb turn_rubric_evaluation
        jsonb client_integrity_telemetry
        int turn_duration_ms
        timestamp committed_at
    }

    LANGGRAPH_STATE_CHECKPOINTS {
        uuidv7 checkpoint_id PK
        uuidv7 session_id FK
        uuidv7 parent_checkpoint_id FK
        uuidv7 tenant_id FK
        int superstep_sequence
        bigint writer_fencing_token
        bytes compressed_state_payload
        jsonb channel_versions_map
        timestamp created_at
    }

    LANGGRAPH_CHECKPOINT_WRITES {
        uuidv7 write_id PK
        uuidv7 checkpoint_id FK
        uuidv7 session_id FK
        string task_node_name
        string channel_name
        int write_sequence
        bytes serialized_channel_value
    }

    CANDIDATE_EVALUATION_REPORTS {
        uuidv7 report_id PK
        uuidv7 session_id FK
        uuidv7 requisition_id FK
        uuidv7 candidate_id FK
        uuidv7 tenant_id FK
        decimal final_weighted_technical_score
        decimal combined_hire_readiness_score
        decimal final_integrity_authenticity_index
        enum hiring_recommendation_band
        text executive_synthesis_summary
        int aggregate_version
        timestamp generated_at
    }

    REPORT_DIMENSION_SCORES {
        uuidv7 dimension_score_id PK
        uuidv7 report_id FK
        uuidv7 dimension_id
        uuidv7 tenant_id FK
        int peak_verified_depth_level
        decimal normalized_dimension_score
        text strengths_citation
        text weaknesses_citation
    }

    REPORT_INTEGRITY_FLAGS {
        uuidv7 integrity_flag_id PK
        uuidv7 report_id FK
        uuidv7 turn_id FK
        uuidv7 tenant_id FK
        enum anomaly_category
        enum severity_level
        decimal confidence_score
        text forensic_evidence_summary
    }

    TRANSACTIONAL_OUTBOX_EVENTS {
        uuidv7 event_id PK
        uuidv7 tenant_id FK
        string aggregate_type
        uuidv7 aggregate_id
        string event_type
        jsonb event_payload
        enum relay_status
        int delivery_attempt_count
        timestamp created_at
        timestamp processed_at
    }

```

### 1.4 Exhaustive Relational Data Dictionary Specifications

#### Table 1.4.1: Recruitment Management Context (`core_screening` Schema)

| Table Name | Column Name | Conceptual Data Type | Key & Constraint Rules | Indexing Strategy | Partitioning & Retention Policy | 
| ----- | ----- | ----- | ----- | ----- | ----- | 
| **`job_requisitions`** | `requisition_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, requisition_id)` | B-Tree PK (Time-ordered locality) | Hash-partitioned by `tenant_id` ($16$ partitions); retained for life of tenant contract. | 
| **`job_requisitions`** | `tenant_id` | `UUIDv7` | `NOT NULL`, `FOREIGN KEY (tenants)` | Partition Key + RLS Policy Filter | Enforces strict tenant isolation. | 
| **`job_requisitions`** | `active_rubric_version_id` | `UUIDv7` | `NOT NULL`, `FK (competency_rubric_versions)` | B-Tree `(tenant_id, active_rubric_version_id)` | Resolves active rubric in $O(1)$ lookup. | 
| **`job_requisitions`** | `seniority_band` | `EnumString(24)` | `CHECK IN ('JUNIOR_L1'..'STAFF_PRINCIPAL_L4')` | B-Tree `(tenant_id, requisition_status, seniority_band)` | Dashboard filtering index. | 
| **`job_requisitions`** | `aggregate_version` | `PositiveInteger` | `NOT NULL DEFAULT 1`, `CHECK >= 1` | Included in PK update predicate | Optimistic Concurrency Control (OCC). | 
| **`competency_rubric_versions`** | `rubric_version_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, rubric_version_id)` | B-Tree PK | Append-only immutable table; never updated in-place once referenced by an evaluation. | 
| **`competency_rubric_versions`** | `version_number` | `PositiveInteger` | `UNIQUE (tenant_id, requisition_id, version_number)` | B-Tree Unique Composite | Tracks rubric calibration history. | 
| **`competency_rubric_versions`** | `weighted_dimensions` | `StructuredJSONB` | `NOT NULL`, Checksum validated ($\sum w_k = 1.0$) | GIN Index (`jsonb_path_ops`) | Stores immutable competency descriptors and weights. | 
| **`screening_batches`** | `batch_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, batch_id)` | B-Tree PK | Retained $365\text{ days}$ active; archived to cold storage thereafter. | 
| **`screening_batches`** | `batch_status` | `EnumString(32)` | `NOT NULL`, State Machine constrained | Partial B-Tree `WHERE batch_status IN ('QUEUED', 'PROCESSING_ACTIVE')` | Accelerates active batch monitoring queries. | 
| **`batch_document_tasks`** | `document_task_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, document_task_id)` | B-Tree PK | Range-partitioned monthly by `created_at`; purged $90\text{ days}$ after batch completion. | 
| **`batch_document_tasks`** | `idempotency_key` | `FixedHexString(64)` | `UNIQUE (tenant_id, requisition_id, content_sha256_hash)` | B-Tree Unique Index | Prevents duplicate CV processing within the same requisition. | 

#### Table 1.4.2: Identity Vault (`identity_vault` Schema) & Document Intelligence Context (`core_screening` Schema)

| Table Name | Column Name | Conceptual Data Type | Key & Constraint Rules | Indexing Strategy | Partitioning & Retention Policy | 
| ----- | ----- | ----- | ----- | ----- | ----- | 
| **`candidate_pii_vault`** | `candidate_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, candidate_id)` | B-Tree PK (Isolated `identity_vault` schema) | Crypto-shredded automatically at `retention_expires_at` (Default $180\text{ days}$ for rejected candidates, configurable per GDPR policy) or immediately upon erasure request. | 
| **`candidate_pii_vault`** | `encrypted_pii_payload` | `ByteArray (AES-256-GCM)` | `NULLABLE` (Set to `NULL` upon crypto-shredding) | Unindexed (Opaque Ciphertext) | Contains Name, Email, Phone, LinkedIn, Raw CV Blob URI. | 
| **`candidate_pii_vault`** | `wrapped_candidate_dek` | `ByteArray (KMS Wrapped)` | `NULLABLE` (Zeroed upon crypto-shredding) | Unindexed | Unique per-candidate AES-256 Data Encryption Key. | 
| **`candidate_pii_vault`** | `blind_email_hmac_index` | `FixedHexString(64)` | `NULLABLE`, `HMAC-SHA256(NormalizedEmail, TenantSalt)` | B-Tree `(tenant_id, blind_email_hmac_index) WHERE NOT is_crypto_shredded` | Enables exact-match duplicate candidate detection across requisitions without decrypting PII. | 
| **`candidate_profiles`** | `candidate_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, candidate_id)` | B-Tree PK | Hash-partitioned by `tenant_id`; retained anonymously for longitudinal analytics. | 
| **`candidate_profiles`** | `vector_sync_version` | `MonotonicInteger` | `NOT NULL DEFAULT 1` | Partial B-Tree `WHERE vector_sync_status != 'SYNCED'` | Drives the Relational $\leftrightarrow$ Vector Store anti-entropy reconciler. | 
| **`candidate_skill_evidence`** | `skill_evidence_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, candidate_id, canonical_skill_id)` | B-Tree Covering Index `(tenant_id, canonical_skill_id, applied_duration_months)` | Accelerates Hard-Skill Gatekeeper relational verification. | 
| **`match_evaluations`** | `match_evaluation_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, match_evaluation_id)` | B-Tree Composite `(tenant_id, requisition_id, assigned_tier, composite_match_score DESC)` | Enables sub-$5\text{ ms}$ cursor pagination of ranked candidate leaderboards. | 
| **`match_evaluations`** | `epistemic_skill_gap_vector` | `StructuredJSONB` | `NOT NULL`, Ordered array of $(\text{DimensionID}, \Pi_k)$ | Read directly by `SessionID` provisioning | Seeds Phase 4 LangGraph initial interview agenda. | 

#### Table 1.4.3: Live Interview, LangGraph Checkpointing & Evaluation Analytics (`core_screening` Schema)

| Table Name | Column Name | Conceptual Data Type | Key & Constraint Rules | Indexing Strategy | Partitioning & Retention Policy | 
| ----- | ----- | ----- | ----- | ----- | ----- | 
| **`interview_sessions`** | `session_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, session_id)` | B-Tree PK + Unique `(tenant_id, requisition_id, candidate_id)` | Hash-partitioned by `tenant_id`; core lifecycle aggregate root. | 
| **`interview_sessions`** | `active_fencing_token` | `BigIntMonotonic` | `NOT NULL DEFAULT 0` | Validated atomically on every turn commit | Split-brain fencing guard across stateless WebSocket pods. | 
| **`interview_turn_ledger`** | `turn_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, session_id, turn_index)` | B-Tree Clustered by `(tenant_id, session_id, turn_index ASC)` | Monthly range-partitioned; append-only immutable audit log of every question, answer, and score delta. | 
| **`langgraph_state_checkpoints`** | `checkpoint_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, session_id, checkpoint_id)` | B-Tree `(tenant_id, session_id, superstep_sequence DESC)` | Monthly range-partitioned; intermediate super-steps compacted $7\text{ days}$ after session completion (retaining only initial and terminal checkpoints). | 
| **`langgraph_state_checkpoints`** | `compressed_state_payload` | `ByteArray (Zstd MessagePack)` | `NOT NULL` | Unindexed binary payload | Zstandard-compressed (`level=3`) serialized `InterviewGraphStateSchema` reducing storage footprint by $\approx 74\%$. | 
| **`candidate_evaluation_reports`** | `report_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, report_id)` | B-Tree `(tenant_id, requisition_id, combined_hire_readiness_score DESC)` | Powers Recruiter Dashboard rankings and Side-by-Side Comparison Matrix queries. | 
| **`report_dimension_scores`** | `dimension_score_id` | `UUIDv7` | `PRIMARY KEY (tenant_id, report_id, dimension_id)` | B-Tree Covering `(tenant_id, report_id, dimension_id, normalized_dimension_score)` | Enables single-index-only scan for 2–4 candidate matrix comparison. | 
| **`transactional_outbox_events`** | `event_id` | `UUIDv7` | `PRIMARY KEY (created_at, event_id)` | Partial B-Tree `(created_at ASC) WHERE relay_status = 'PENDING'` | Daily range-partitioned; partitions dropped automatically via `DROP TABLE` $72\text{ hours}$ after 100% relay confirmation (zero table bloat). | 

## 2. Vector Store Architecture & Cross-Store Consistency

### 2.1 Multi-Tenant Vector Collection Design & Quantization Mathematics

The Vector Store (`Qdrant` / Hybrid Vector Engine) hosts two primary collections:

1. **`candidate_evidence_chunks_v1`:** Stores the contextualized atomic Child Chunks extracted from candidate resumes (Phase 3, ADR-004).

2. **`job_competency_anchors_v1`:** Caches the dense and sparse representations of each `JobCompetencyRubric` dimension's `IdealAnchorDescriptor` so batch evaluations of $500$ CVs do not re-embed the Job Description on every candidate match.

#### Mathematical Formulation of Vector Memory & Storage Sizing

For a platform scaling to $M_{\text{cand}} = 1,000,000$ active candidates with an average of $\bar{c} = 18$ contextualized child chunks per candidate ($N_{\text{vecs}} = 1.8 \times 10^7$ points) and dense embedding dimension $d = 1536$:

1. **Uncompressed Float32 Memory Footprint (**$B_{\text{raw}}$**):** 

   $$
   B_{\text{raw}} = N_{\text{vecs}} \times d \times 4\text{ bytes} = 1.8 \times 10^7 \times 1536 \times 4 \approx 110.59\text{ GB}
   $$

2. **Scalar Quantized (`INT8`) In-RAM HNSW Footprint (**$B_{\text{SQ8}}$**):** Each $32$-bit float component $x_i \in [x_{\min}, x_{\max}]$ is mapped to an $8$-bit integer $q_i \in [-127, 127]$ via quantile-clipped affine scaling, keeping the quantized vectors and HNSW graph links ($M = 16$, $8\text{ bytes/link}$) in RAM while storing original `Float32` vectors on memory-mapped NVMe SSD (`mmap`) for top-$K$ oversampled rescoring (oversampling factor $\eta_{\text{rescore}} = 3.0$): 

   $$
   B_{\text{RAM\_SQ8}} = N_{\text{vecs}} \times \left( d \times 1\text{ byte} + 2 \cdot M \times 8\text{ bytes} \right) \times 1.15 \approx 1.8 \times 10^7 \times (1536 + 256) \times 1.15 \approx 37.09\text{ GB}
   $$

    This achieves a $66.5\%$ RAM reduction while maintaining $> 0.994$ Recall@10 parity with uncompressed `Float32` search.

### 2.2 Synchronization Saga & Eventual Consistency (PostgreSQL $\leftrightarrow$ Vector DB)

Because PostgreSQL and the Vector Store are distinct distributed storage engines that do not share a two-phase commit (2PC) protocol, direct dual writes from an application use case risk permanent state divergence (e.g., a candidate is deleted or updated in PostgreSQL, but orphan vectors remain searchable in the Vector Store).

To enforce **Deterministic Eventual Consistency** with a strict upper bound on synchronization lag, the platform implements an **Outbox-Driven Idempotent Vector Synchronization & Anti-Entropy Architecture**:

1. **Monotonic Vector Sync Versioning:** Every mutation to a `CandidateProfile` in PostgreSQL increments `vector_sync_version = v + 1`, sets `vector_sync_status = 'PENDING_SYNC'`, and inserts a `CandidateVectorSyncRequestedEvent` (carrying version $v + 1$) into `transactional_outbox_events` within the **same ACID transaction**.

2. **Version-Guarded Idempotent Upsert:** When the Vector Sync Worker consumes the event for `(candidate_id, version = v + 1)`:

   * It queries the Vector Store for any existing points matching `Payload.candidate_id` with `Payload.sync_version >= v + 1`. If found, the message is a stale out-of-order delivery and is acknowledged as a no-op.

   * Otherwise, it upserts the new child chunks tagged with `Payload.sync_version = v + 1`, deletes any stale points for that `candidate_id` where `Payload.sync_version < v + 1`, and updates PostgreSQL: 

     $$
     \text{UPDATE } \text{candidate\_profiles SET vector\_sync\_status = 'SYNCED' WHERE candidate\_id = } c \land \text{vector\_sync\_version = } v + 1
     $$

3. **Cryptographic Erasure & Tombstone Purge:** When `CandidatePIIErasedEvent` is emitted during GDPR crypto-shredding, the Vector Sync Worker executes a hard filter-delete across `candidate_evidence_chunks_v1` for `Payload.tenant_id == T AND Payload.candidate_id == c` and records a cryptographic purge receipt.

4. **Periodic Anti-Entropy Reconciliation Sweep:** Every $5\text{ minutes}$, a background `VectorConsistencyReconciler` queries PostgreSQL for any `candidate_profiles` where `vector_sync_status != 'SYNCED' AND extracted_at < NOW() - INTERVAL '60 seconds'` and re-triggers idempotent synchronization, guaranteeing self-healing recovery even if the message broker experiences a transient failure.

### 2.3 Visual Architecture Diagram: Cross-Store Synchronization & Anti-Entropy Saga

```
sequenceDiagram
    autonumber
    participant UseCase as Application: ProcessCVDocumentUseCase
    participant PG as Infrastructure: PostgreSQL (Core Schema)
    participant Outbox as Infrastructure: OutboxRelayDaemon
    participant Broker as Infrastructure: DistributedTaskBroker
    participant SyncWorker as Infrastructure: VectorSyncWorkerAdapter
    participant Qdrant as Infrastructure: HybridVectorStore (Qdrant)
    participant Reconciler as Infrastructure: AntiEntropyReconcilerDaemon

    Note over UseCase,PG: Atomic Relational Transaction (ACID)
    UseCase->>PG: BEGIN TRANSACTION
    UseCase->>PG: Upsert CandidateProfile (vector_sync_version = v, status = PENDING_SYNC)
    UseCase->>PG: Insert OutboxEvent (CandidateVectorSyncRequested, version = v)
    UseCase->>PG: COMMIT TRANSACTION

    Outbox->>PG: Poll Pending Outbox Events (FOR UPDATE SKIP LOCKED)
    PG-->>Outbox: Return CandidateVectorSyncRequested (version = v)
    Outbox->>Broker: Publish Event to VectorSyncQueue
    Outbox->>PG: Mark OutboxEvent status = RELAYED

    Broker->>SyncWorker: Deliver CandidateVectorSyncRequested (CandidateID, version = v)
    SyncWorker->>Qdrant: Upsert Child Chunks (PointID = UUIDv5, Payload.sync_version = v)
    Qdrant-->>SyncWorker: Upsert Acknowledged (Wait = True)
    SyncWorker->>Qdrant: Delete Stale Points WHERE candidate_id == C AND sync_version < v
    Qdrant-->>SyncWorker: Stale Points Purged
    SyncWorker->>PG: UPDATE candidate_profiles SET status = SYNCED WHERE candidate_id = C AND version = v

    Note over PG,Reconciler: Self-Healing Anti-Entropy Loop (Every 5 Minutes)
    Reconciler->>PG: SELECT candidate_id, vector_sync_version WHERE status != SYNCED AND age > 60s
    alt Unsynced Stragglers Found (Broker Drop or Worker Crash)
        PG-->>Reconciler: Return Straggler Candidate List
        Reconciler->>Broker: Re-Enqueue Idempotent Sync Tasks (version = v)
    end

```

## 3. LangGraph Checkpointer & Distributed State Persistence

### 3.1 Architecture Decision Record: ADR-009 (Two-Tier Hybrid Checkpointer & Monotonic Fencing Tokens)

| ADR Field | Specification Details | 
| ----- | ----- | 
| **Identifier** | **ADR-009: Two-Tier Write-Through LangGraph Checkpointer (Redis L1 + PostgreSQL L2) with Split-Brain Fencing Tokens** | 
| **Status** | Accepted & Enforced | 
| **Context** | In Phase 4 and Phase 5, the stateful LangGraph interview engine executes multiple cognitive nodes (`EvaluationNode` $\rightarrow$ `ConditionalRouter` $\rightarrow$ `SocraticFollowUpNode`) within a strict conversational latency budget ($P_{95} \le 1,850\text{ ms}$). Relying solely on synchronous PostgreSQL disk reads/writes on every intermediate graph super-step adds $25\text{--}60\text{ ms}$ of serialization and network I/O overhead per node transition. Conversely, storing graph state exclusively in Redis or local WebSocket pod memory risks permanent state loss during a Redis node restart and suffers from "split-brain" corruption if a candidate's mobile network switches from Wi-Fi to 5G—opening a new WebSocket on Pod B while Pod A is still finishing an in-flight LLM generation for the same `SessionID`. | 
| **Decision** | 1\. **Two-Tier Hybrid Checkpointer (`HybridTieredLangGraphCheckpointer`):**     - **L1 Hot State Cache (Redis Cluster):** Stores the active uncompressed/LZ4-fast `InterviewGraphStateSchema` keyed by `lg:ckpt:{tenant_id}:{session_id}:latest` with a sliding TTL of $3,600\text{ seconds}$, enabling $< 1.5\text{ ms}$ state reads between super-steps.     - **L2 Durable Checkpoint Ledger (PostgreSQL `langgraph_state_checkpoints`):** Every completed conversational turn boundary (and any terminal or error state) is synchronously committed to PostgreSQL using Zstandard compression, while intermediate intra-turn node transitions are pipelined asynchronously.  2\. **Monotonic Fencing Token Concurrency Guard (**$\mathcal{F}_n$**):** Whenever a WebSocket gateway pod acquires or renews ownership of an `InterviewSession`, Redis atomically increments a monotonic $64$-bit session epoch counter (`INCR session:{session_id}:fencing_token`) yielding fencing token $\mathcal{F}_n$. Every LangGraph checkpoint write and PostgreSQL state mutation must pass $\mathcal{F}_n$. If a zombie task on an evicted pod attempts to commit a checkpoint with a stale token $\mathcal{F}_{\text{stale}} < \mathcal{F}_{\text{current}}$, both Redis (via Lua script guard) and PostgreSQL (`WHERE active_fencing_token <= :writer_token`) immediately reject the write with a `StaleFencingTokenRejectedError`. | 
| **Alternatives Considered** | 1\. *Sticky Load Balancer Sessions + In-Memory Dict Checkpointer:* Rejected because any pod deployment, auto-scaling scale-in, or OOM restart destroys active live interviews.  2\. *Distributed Advisory Locks Only (Without Fencing Tokens):* Rejected because a garbage-collection pause or network delay on Pod A can cause its lock lease to expire while its LLM call is in flight; when the LLM call returns, Pod A would overwrite Pod B's state unless fenced by a monotonically increasing token at the storage layer. | 
| **Consequences** | **Positive:** Combines sub-$2\text{ ms}$ L1 hot-read speed with ACID relational durability; mathematically eliminates split-brain state corruption during WebSocket reconnections.  **Trade-offs:** Requires implementing a custom adapter conforming to the LangGraph checkpointer interface behind our `InterviewStateCheckpointerPort`. | 

### 3.2 Mathematical Formulation of Split-Brain Fencing & Checkpoint Compaction

#### 1. Monotonic Fencing Invariant

Let $\mathcal{F}(s, t) \in \mathbb{N}^+$ denote the fencing token issued to the active WebSocket connection handler for session $s$ at connection epoch $t$, satisfying strict monotonicity:

$$
t_2 > t_1 \implies \mathcal{F}(s, t_2) \ge \mathcal{F}(s, t_1) + 1
$$

Let $\mathcal{F}_{\text{DB}}(s)$ be the `active_fencing_token` stored on the `interview_sessions` Aggregate Root in PostgreSQL. Any state checkpoint write request $W = (s, \text{checkpoint}_k, \mathcal{F}_{\text{writer}})$ is governed by the atomic transition predicate $\mathcal{P}_{\text{fence}}$:

$$
\mathcal{P}_{\text{fence}}(W) = \begin{cases} \text{COMMIT} \land \left(\mathcal{F}_{\text{DB}}(s) \leftarrow \mathcal{F}_{\text{writer}}\right) & \text{if } \mathcal{F}_{\text{writer}} \ge \mathcal{F}_{\text{DB}}(s) \\ \text{ABORT\_STALE\_WRITER} & \text{if } \mathcal{F}_{\text{writer}} < \mathcal{F}_{\text{DB}}(s) \end{cases}
$$

#### 2. Post-Interview Checkpoint Compaction Ratio

During a $12$-turn adaptive interview where each turn executes $3$ LangGraph super-steps (Input Ingestion $\rightarrow$ `EvaluationNode` $\rightarrow$ `QuestionGeneratorNode` / `SocraticFollowUpNode`), the checkpointer records $N_{\text{steps}} = 36$ checkpoint snapshots. Once `InterviewSession` reaches a terminal state (`COMPLETED` or `TERMINATED_EARLY`) and the $7$-day forensic debugging window elapses, the `CheckpointCompactionJob` prunes intermediate intra-turn super-steps while preserving:

* $C_0$: Initial bootstrap state snapshot.

* $\{C_{\text{turn}, 1}, \dots, C_{\text{turn}, T}\}$: Committed turn-boundary checkpoints (optional configurable policy) or solely $C_{\text{terminal}}$ (since `interview_turn_ledger` already preserves every turn's full input, output, score, and telemetry in normalized relational rows).

This reduces long-term checkpoint table storage by:

$$
\eta_{\text{compaction}} = 1 - \frac{2}{N_{\text{steps}}} = 1 - \frac{2}{36} \approx 94.4\%
$$

### 3.3 Visual Architecture Diagram: Distributed Checkpointing & Failover Recovery Sequence

```
sequenceDiagram
    autonumber
    actor Candidate as Candidate Client
    participant PodA as WS Gateway Pod A (Initial)
    participant PodB as WS Gateway Pod B (Reconnect)
    participant Redis as L1 Hot Cache & Fencing Coordinator
    participant LGACL as Infrastructure: LangGraphOrchestratorACL
    participant PG as L2 Durable Store: PostgreSQL

    Note over Candidate,PodA: Normal Turn Execution on Pod A (Fencing Token = 101)
    Candidate->>PodA: CLIENT_TURN_COMMIT (TurnIndex = 3)
    PodA->>LGACL: ExecuteTurn(SessionID, TurnIndex = 3, FencingToken = 101)
    LGACL->>Redis: ReadHotCheckpoint(SessionID) [Latency < 1.5ms]
    Redis-->>LGACL: Return Checkpoint (Superstep = 8)
    LGACL->>LGACL: Run EvaluationNode (Superstep = 9)

    Note over Candidate,PodB: Candidate Wi-Fi Drops & Reconnects to Pod B Mid-Turn!
    Candidate--xPodA: Socket Disconnected
    Candidate->>PodB: WSS Reconnect (SessionID, LastReceivedServerSeq)
    PodB->>Redis: Atomic AcquireLease & INCR FencingToken(SessionID)
    Redis-->>PodB: Issued New FencingToken = 102 (Evicts Pod A Lease)
    PodB->>PG: Update interview_sessions SET active_fencing_token = 102

    Note over PodA,PG: Zombie Coroutine on Pod A Finishes LLM Call & Attempts Write with Stale Token 101
    LGACL->>PG: Attempt SaveCheckpoint(Superstep = 9, WriterFencingToken = 101)
    PG-->>LGACL: REJECTED (WriterToken 101 < ActiveFencingToken 102)
    LGACL-->>PodA: Raise StaleFencingTokenRejectedError -> Terminate Zombie Task Cleanly

    Note over PodB,PG: Pod B Restores Clean Committed Checkpoint & Resumes Turn 3 Safely
    PodB->>Redis: FetchLatestVerifiedCheckpoint(SessionID, RequiredToken = 102)
    alt L1 Cache Miss or Uncommitted Partial Step
        PodB->>PG: LoadLastCommittedTurnCheckpoint(SessionID, TurnIndex = 2)
        PG-->>PodB: Return Durable Checkpoint (Superstep = 8)
    end
    PodB-->>Candidate: SERVER_SESSION_RESUMED (Clean State Restored, Zero Corruption)

```

### 3.4 Outbound Application Port Specifications (Persistence & Checkpointing)

| Port Interface Name | Method Signature (Conceptual) | Preconditions & Input Contract | Postconditions & Output Contract | Failure Modes & Domain Exceptions | 
| ----- | ----- | ----- | ----- | ----- | 
| **`UnitOfWorkPort`** | `ExecuteAtomicTransaction(TenantID, TransactionalWorkCallback) -> TransactionResult` | Authenticated `TenantID` non-null; opens database session and sets `SET LOCAL app.current_tenant_id = :tenant_id`. | Commits all aggregate mutations and `TransactionalOutboxEvent` records atomically, or rolls back 100% on any exception. | `ConcurrencyConflictDomainError` (OCC version mismatch), `DeadlockSerializationError`, `DatabaseUnavailableError`. | 
| **`CandidateIdentityVaultPort`** | `StoreAndTokenizePII(TenantID, RawCandidatePII) -> AnonymizedCandidateToken` | Invoked exclusively during Stage 3 ingestion or authorised Recruiter identity resolution. | Generates per-candidate AES-256-GCM DEK, wraps DEK via KMS, persists ciphertext in `identity_vault`, and returns surrogate `CandidateID`. | `KMSWrappingFailureError`, `DuplicateCandidateIdentityError` (matched via `blind_email_hmac_index`). | 
| **`CandidateIdentityVaultPort`** | `ExecuteCryptoShredding(TenantID, CandidateID, ErasureReason) -> CryptoShredAuditReceipt` | Authorized GDPR erasure request or automated retention expiration policy trigger. | Permanently zeroes `wrapped_candidate_dek` and `encrypted_pii_payload`, sets `is_crypto_shredded = TRUE`, and emits `CandidatePIIErasedEvent`. | `CandidateAlreadyShreddedNotice` (Idempotent success), `VaultAccessDeniedError`. | 
| **`InterviewStateCheckpointerPort`** | `SaveSuperStepCheckpoint(TenantID, SessionID, FencingToken, CheckpointSnapshot) -> CheckpointReceipt` | `FencingToken` must satisfy $\mathcal{F}_{\text{writer}} \ge \mathcal{F}_{\text{DB}}(s)$; `CheckpointSnapshot` validated against `InterviewGraphStateSchema`. | Atomically updates L1 Redis hot state and commits Zstd-compressed snapshot to `langgraph_state_checkpoints`. | `StaleFencingTokenRejectedError`, `CheckpointSerializationError`, `StorageQuotaExceededError`. | 
| **`InterviewStateCheckpointerPort`** | `LoadLatestCheckpoint(TenantID, SessionID, FencingToken) -> RestoredCheckpointState` | Active `SessionID` owned by `TenantID`; caller holds valid `FencingToken`. | Returns hydrated state from L1 Redis cache if valid, or falls back transparently to L2 PostgreSQL durable ledger and warms L1 cache. | `CheckpointNotFoundError`, `CorruptedStatePayloadError`. | 
