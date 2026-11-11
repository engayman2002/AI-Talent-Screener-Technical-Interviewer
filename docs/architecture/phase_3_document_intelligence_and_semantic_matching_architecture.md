# AI Talent Screener & Technical Interviewer

## Phase 3: Document Intelligence & Semantic Matching Architecture (LangChain Design)

* **Document ID:** `ARCH-SPEC-PHASE-03`

* **Architectural Paradigm:** Clean Architecture, Hexagonal Ports & Adapters, Tactical DDD, Asynchronous Event-Driven Ingestion

* **Implementation Policy:** Strict Zero-Code Conceptual & Mathematical Specification

* **Bounded Contexts Covered:** *Document Intelligence Context* (Upstream Producer) $\rightarrow$ *Recruitment Management Context* & *Live Interview Context* (Downstream Consumers)

## 1. Asynchronous Ingestion Pipeline Topology

### 1.1 Architectural Overview & Clean Architecture Layer Mapping

To satisfy the non-functional throughput target of processing $500\text{ CVs within } 180\text{ seconds}$ ($\Theta_{\text{batch}} \ge 2.77\text{ CVs/sec}$ at $P_{95}$) without blocking the FastAPI main event loop, document ingestion is architected as a **staged, idempotent, asynchronous event-driven pipeline**.

Every stage of the pipeline is decoupled via **Application Layer Outbound Driven Ports**, ensuring that the **Domain Layer** (*Document Intelligence Context*) remains completely agnostic of binary file formats (PDF, DOCX), OCR engines, PII vault encryption mechanisms, and LangChain extraction chains.

| Pipeline Stage | Clean Architecture Layer | Responsible Component / Port | Core Architectural Responsibility | 
| ----- | ----- | ----- | ----- | 
| **Stage 0: Batch Reception & Pre-Signed Offload** | Presentation $\rightarrow$ Application | `BatchIngestionController` $\rightarrow$ `InitiateScreeningBatchUseCase` | Validates batch metadata, enforces tenant rate limits, persists `ScreeningBatch` aggregate in `INITIATED` state, and issues cryptographic pre-signed Object Storage upload URIs or streams multipart chunks directly to immutable blob storage. | 
| **Stage 1: Malware & Magic-Byte Quarantine** | Application $\rightarrow$ Infrastructure | `VirusScannerPort` $\rightarrow$ `ClamAVStreamAdapter` | Verifies file magic bytes (strictly enforcing PDF `%PDF-` and OOXML `PK\x03\x04` signatures), checks SHA-256 content hashes for batch-level deduplication, and scans for embedded macros or polyglot payloads before permitting downstream parsing. | 
| **Stage 2: Layout-Aware Multi-Modal Parsing** | Application $\rightarrow$ Infrastructure | `DocumentLayoutParserPort` $\rightarrow$ `UnstructuredLayoutAdapter` | Reconstructs reading order across multi-column resumes, tables, and sidebars; executes OCR fallback ONLY on rasterized page regions where character density falls below threshold $\rho_{\text{char}} < 15\text{ chars/inch}^2$; emits a structural `NormalizedDocumentTree` Value Object. | 
| **Stage 3: Zero-Trust PII Tokenization & Vaulting** | Application $\rightarrow$ Infrastructure | `PIIRedactionVaultPort` $\rightarrow$ `PresidioCryptographicVaultAdapter` | Detects and strips candidate Personally Identifiable Information (Full Name, Email, Phone, Physical Address, LinkedIn/GitHub URLs, Demographic/Gender indicators, University Graduation Years if configured for blind screening) and replaces them with deterministic surrogate tokens (`<CANDIDATE_ID_TOKEN>`) prior to LLM context injection. | 
| **Stage 4: Grounded Structured Entity Extraction** | Application $\rightarrow$ Infrastructure | `CandidateDossierExtractionPort` $\rightarrow$ `LangChainStructuredExtractorACL` | Executes a schema-constrained LangChain extraction pipeline over the PII-redacted markdown tree, requiring verbatim source quotes for every extracted skill and metric to prevent LLM hallucination; translates raw output into the `CandidateProfile` Aggregate Root. | 
| **Stage 5: Multi-Granular Embedding & Indexing** | Application $\rightarrow$ Infrastructure | `VectorIndexPort` $\rightarrow$ `QdrantHybridIndexAdapter` | Segments the validated `CandidateProfile` into hierarchical Parent-Child semantic chunks, computes dense embeddings and sparse lexical (BM25/SPLADE) vectors, and upserts them with tenant-isolated payload metadata. | 
| **Stage 6: Hybrid Scoring & Gap Vector Synthesis** | Application $\rightarrow$ Domain | `EvaluateCandidateMatchUseCase` $\rightarrow$ `HybridMatchingDomainService` | Executes the deterministic mathematical matching model against the `JobRequisition` rubric, calculates tier classifications, synthesizes the `EpistemicSkillGapVector` for Phase 4 interviewing, and publishes `CandidateScreeningCompletedEvent`. | 

### 1.2 Visual Architecture Diagram: End-to-End Asynchronous Ingestion Sequence

```
sequenceDiagram
    autonumber
    actor Recruiter as Technical Recruiter
    participant API as Presentation: BatchRESTController
    participant InitUC as Application: InitiateBatchUseCase
    participantObj as Infrastructure: QuarantineObjectStore
    participant Queue as Infrastructure: DistributedTaskBroker
    participant IngestUC as Application: ProcessCVDocumentUseCase
    participant Scanner as Infrastructure: VirusAndMimeAdapter
    participant Parser as Infrastructure: LayoutAwarePDFDocxAdapter
    participant PIIVault as Infrastructure: PIIAnonymizationVaultAdapter
    participant LCExtractor as Infrastructure: LangChainExtractionACL
    participant VecStore as Infrastructure: HybridVectorStoreAdapter
    participant MatchSvc as Domain: HybridMatchingDomainService
    participant Repo as Infrastructure: PostgreSQLRepositoryAdapter

    Recruiter->>API: POST /api/v1/requisitions/{id}/batches (Multipart/Manifest)
    API->>InitUC: Execute(InitiateBatchCommandDTO)
    InitUC->>Repo: Persist ScreeningBatch (State = QUEUED, Count = N)
    InitUC->>participantObj: Stream Raw Files to Quarantine Bucket
    InitUC->>Queue: Fan-Out N Idempotent DocumentTaskPayloads (BatchID, DocHash)
    InitUC-->>API: Return 202 Accepted (BatchID, StatusStreamURI)
    API-->>Recruiter: 202 Accepted + SSE Progress Tracking Handle

    par Parallel Worker Execution per Document (Bounded Concurrency)
        Queue->>IngestUC: Consume(DocumentTaskPayload)
        IngestUC->>Scanner: ScanAndVerifySignature(QuarantineURI, ExpectedSHA256)
        alt Malware Detected or Invalid Magic Bytes
            Scanner-->>IngestUC: Return SecurityViolationFailure
            IngestUC->>Repo: Mark Document QUARANTINED_REJECTED + Emit AuditEvent
        else Signature Verified & Clean
            Scanner-->>IngestUC: Return VerifiedBinaryStream
            IngestUC->>Parser: ExtractLayoutTree(VerifiedBinaryStream)
            Parser-->>IngestUC: Return NormalizedDocumentTree (Markdown + BoundingBoxes)
            
            IngestUC->>PIIVault: AnonymizeAndVaultPII(NormalizedDocumentTree, TenantID)
            PIIVault-->>IngestUC: Return AnonymizedDocumentTree + PIIVaultReferenceID
            
            IngestUC->>LCExtractor: ExtractGroundedCandidateDossier(AnonymizedDocumentTree)
            Note over IngestUC,LCExtractor: ACL Boundary: LangChain Runnable & Retry Logic Hidden
            LCExtractor-->>IngestUC: Return Validated ExtractedCandidateDossier (Value Object)
            
            IngestUC->>VecStore: IndexHierarchicalChunks(CandidateID, ExtractedCandidateDossier)
            VecStore-->>IngestUC: Confirm Vector Upsert (Dense + Sparse Vectors)
            
            IngestUC->>MatchSvc: ComputeHybridMatch(ExtractedCandidateDossier, JobCompetencyRubric)
            MatchSvc-->>IngestUC: Return SemanticMatchScore + EpistemicSkillGapVector + TierDecision
            
            IngestUC->>Repo: Atomic Commit (CandidateProfile + MatchEvaluation + BatchProgressIncrement)
            IngestUC->>Queue: Publish CandidateScreeningCompletedEvent
        end
    end

```

### 1.3 Document Lifecycle State Machine & Failure Recovery Topology

Each individual document within a `ScreeningBatch` is governed by an explicit state machine (`DocumentProcessingState`) to guarantee zero silent data loss and deterministic retry behavior under transient LLM rate limits or infrastructure brownouts.

```
stateDiagram-v2
    [*] --> UPLOADED_QUARANTINE : File Stream Received
    UPLOADED_QUARANTINE --> VIRUS_SCANNING : Worker Claimed Task (Idempotency Lock)
    VIRUS_SCANNING --> SECURITY_QUARANTINED : Malware / Invalid Magic Byte (Terminal)
    VIRUS_SCANNING --> DUPLICATE_SKIPPED : Content SHA-256 Already Exists in Requisition (Terminal)
    VIRUS_SCANNING --> LAYOUT_PARSING : Clean Signature Verified
    
    LAYOUT_PARSING --> OCR_FALLBACK_PROCESSING : Text Density < 15 chars/sq.in
    OCR_FALLBACK_PROCESSING --> PII_REDACTION : OCR Layout Reconstructed
    LAYOUT_PARSING --> PII_REDACTION : Native Vector Text Extracted
    LAYOUT_PARSING --> UNREADABLE_CORRUPT_FILE : Encrypted / Corrupted Stream (Terminal)
    
    PII_REDACTION --> STRUCTURED_EXTRACTION : PII Tokenized & Vaulted
    
    STRUCTURED_EXTRACTION --> RETRY_BACKOFF_WAIT : LLM 429 Rate Limit / 5xx Timeout / Validation Error
    RETRY_BACKOFF_WAIT --> STRUCTURED_EXTRACTION : Exponential Backoff + Jitter Elapsed (Attempt <= 4)
    RETRY_BACKOFF_WAIT --> DEAD_LETTER_QUEUE : Max Retries Exhausted (Attempt > 4)
    
    STRUCTURED_EXTRACTION --> VECTOR_INDEXING : Dossier Schema & Evidence Anchors Validated
    VECTOR_INDEXING --> HYBRID_SCORING : Parent-Child Vectors Upserted
    HYBRID_SCORING --> COMPLETED_SCORED : Match Score & Skill Gap Vector Persisted (Terminal)
    
    DEAD_LETTER_QUEUE --> LAYOUT_PARSING : Manual Recruiter/Admin Replay Triggered

```

### 1.4 Fault Tolerance, Backpressure & Retry Mathematics

1. **Content-Addressable Idempotency Invariant:** Every uploaded document is assigned a deterministic idempotency key $K_{\text{idem}}$ prior to worker dispatch: 

   $$
   K_{\text{idem}} = \text{SHA256}\left( \text{TenantID} \parallel \text{RequisitionID} \parallel \text{SHA256}(\text{RawFileBytes}) \right)
   $$

    If a worker crashes mid-execution and the task broker redelivers the message, the `ProcessCVDocumentUseCase` inspects the last committed checkpoint state for $K_{\text{idem}}$ and resumes from the first incomplete stage rather than re-executing expensive OCR or LLM extraction calls.

2. **Decorrelated Exponential Backoff with Full Jitter:** When the `LangChainStructuredExtractorACL` encounters transient upstream LLM provider rate limits ($\text{HTTP } 429$) or schema validation failures requiring self-correction, retry delay $\Delta t_k$ for attempt $k \in \{1, 2, 3, 4\}$ is governed by: 

   $$
   \Delta t_k = \min\left( T_{\max}, \text{Uniform}\left(0, T_{\text{base}} \times 2^k\right) \right)
   $$

    Where $T_{\text{base}} = 1.5\text{ seconds}$ and $T_{\max} = 30.0\text{ seconds}$.

3. **Token-Bucket Provider Failover & Circuit Breaking:** The `LangChainStructuredExtractorACL` wraps primary and secondary LLM providers behind a stateful circuit breaker. If the rolling error rate over a $60\text{-second}$ sliding window exceeds $\epsilon_{\text{trip}} = 0.15$ (minimum $20$ requests) or $P_{95}$ extraction latency exceeds $12.0\text{ seconds}$, the circuit transitions from `CLOSED` to `OPEN` and dynamically routes extraction chains to the fallback LLM provider adapter while preserving the exact same Outbound Port contract.

### 1.5 Outbound Application Port Specifications (Ingestion Pipeline)

| Port Interface Name | Method Signature (Conceptual) | Preconditions & Input Contract | Postconditions & Output Contract | Failure Modes & Domain Exceptions | 
| ----- | ----- | ----- | ----- | ----- | 
| **`VirusScannerPort`** | `VerifyAndScanStream(BlobReferenceURI, ExpectedSHA256) -> SecurityScanReceipt` | `BlobReferenceURI` points to an unparsed object in Quarantine storage; file size $\le 15\text{ MB}$. | Confirms zero malware signatures and verifies magic byte header matches declared `.pdf` or `.docx` container. | `MaliciousPayloadDetectedError`, `UnsupportedMimeSignatureError`, `ChecksumMismatchError`. | 
| **`DocumentLayoutParserPort`** | `ParseBinaryToLayoutTree(VerifiedBlobURI, OcrPolicy) -> NormalizedDocumentTree` | File has passed `VirusScannerPort` verification; `OcrPolicy` specifies language packs and DPI thresholds. | Returns hierarchical document sections preserving table cells, bullet nesting, page coordinates, and character count metrics. | `EncryptedDocumentError`, `ZeroTextExtractableError`, `DocumentPageLimitExceededError` (if pages $> 20$). | 
| **`PIIRedactionVaultPort`** | `RedactAndVault(NormalizedDocumentTree, TenantEncryptionContext) -> AnonymizedDocumentBundle` | Input document tree contains raw unredacted text; `TenantEncryptionContext` supplies envelope KMS key ID. | All PII entities are replaced with irreversible structural placeholders; original PII is AES-256-GCM encrypted in the isolated Identity Vault. | `KMSKeyUnavailableError`, `PIIConfidenceBelowSafetyThresholdError`. | 
| **`CandidateDossierExtractionPort`** | `ExtractCandidateDossier(AnonymizedDocumentTree, ExtractionOntologyVersion) -> ExtractedCandidateDossier` | Input text is strictly free of direct PII; ontology version matches active domain skill taxonomy. | Returns a fully populated, evidence-anchored `ExtractedCandidateDossier` Value Object with zero unverified fields. | `SchemaValidationExhaustedError`, `UpstreamLLMProviderOutageError`, `PromptInjectionInResumeDetectedError`. | 

## 2. Conceptual Extraction Contracts (CVs & Job Descriptions)

### 2.1 Anti-Hallucination & Evidence-Grounded Extraction Governance

A critical failure mode in LLM-based resume parsing is **extractive hallucination**—where an LLM infers skills a candidate never claimed (e.g., assuming a candidate knows *Kubernetes* because they worked at a cloud company) or fabricates tenure durations when dates are ambiguous.

To enforce deterministic accuracy inside the `LangChainStructuredExtractorACL`, every extracted competency and experience claim is governed by three **Extractive Grounding Invariants**:

1. **Mandatory Verbatim Evidence Span (`VerbatimEvidenceQuote`):** Every extracted skill or project metric MUST include an exact substring quote from the source `AnonymizedDocumentTree` and its section origin. During ACL post-processing, a deterministic string-similarity verifier checks that the Levenshtein token overlap between `VerbatimEvidenceQuote` and the source text is $\ge 0.90$. Any skill failing this verification is discarded as a hallucination.

2. **Explicit Date & Tenure Arithmetic Isolation:** The LLM is forbidden from calculating total years of experience in free text. Instead, the LLM extracts only explicit `StartDate` (Year-Month) and `EndDate` (Year-Month or `PRESENT`) per role. Total duration and recency decay are computed deterministically by the **Domain Layer** (`ExperienceTenureCalculator`).

3. **Epistemic Uncertainty Flagging (`EpistemicGapSignal`):** Whenever a resume contains high-level buzzwords without architectural specifics, metric quantification, or clear individual contribution (e.g., *"Worked on microservices using Kafka"* without stating throughput, partitioning strategy, or ownership scope), the extractor emits an `EpistemicGapSignal` that directly seeds the Phase 4 LangGraph `QuestionGenerator` Node.

### 2.2 CV Extraction Data Contract: `ExtractedCandidateDossier`

#### Table 2.2.1: Root Dossier & Normalized Work History Schema

| Field Name | Conceptual Data Type | Constraints & Validation Rules | Domain Purpose & Downstream Usage | 
| ----- | ----- | ----- | ----- | 
| `DossierSchemaVersion` | `SemanticVersionString` | Format `MAJOR.MINOR.PATCH`; mandatory non-null. | Ensures backward compatibility across evolving extraction ontologies. | 
| `AnonymizedCandidateToken` | `UUIDv7` | Strictly matches the surrogate token issued by `PIIRedactionVaultPort`. | Links cognitive evaluation artifacts to the candidate without exposing PII to AI layers. | 
| `TotalVerifiedExperienceMonths` | `NonNegativeInteger` | Computed deterministically via union of non-overlapping intervals $\bigcup [t_{\text{start}, i}, t_{\text{end}, i}]$; range $[0, 600]$. | Prevents double-counting concurrent freelance/part-time roles; feeds Seniority Alignment calculation. | 
| `CurrentOrMostRecentSeniorityLevel` | `SeniorityTierEnum` | Enum: `{INTERN, JUNIOR_L1, MID_L2, SENIOR_L3, STAFF_PRINCIPAL_L4, MANAGER_DIRECTOR}`. | Anchors baseline interview difficulty calibration in Phase 4. | 
| `WorkHistoryEntries` | `OrderedList<WorkHistoryEntry>` | Ordered chronologically descending; $\ge 0$ entries. | Captures role-by-role professional trajectory and domain relevance. | 
| `WorkHistoryEntry.EntryID` | `StringIdentifier` | Deterministic hash of `(CompanyToken, RoleTitle, StartYearMonth)`. | Referenced as a foreign key by extracted skills and projects to prove where a skill was applied. | 
| `WorkHistoryEntry.NormalizedRoleTitle` | `CanonicalRoleString` | Mapped to internal taxonomy (e.g., `BACKEND_ENGINEER`, `AI_ML_ENGINEER`, `DEVOPS_SRE`). | Enables role-specific relevance weighting during hybrid matching. | 
| `WorkHistoryEntry.StartYearMonth` | `YearMonthTuple` | `Year` $\in [1975, \text{CurrentYear}]$, `Month` $\in [1, 12]$ (defaults to $6$ if only year stated, with `DatePrecisionFlag = YEAR_ONLY`). | Anchors tenure duration and recency decay computation. | 
| `WorkHistoryEntry.EndYearMonth` | `YearMonthTuple | PRESENT` | Must satisfy $\text{EndYearMonth} \ge \text{StartYearMonth}$. | Determines whether skill application is active ($\Delta t = 0$) or historical ($\Delta t > 0$). | 
| `WorkHistoryEntry.EmploymentType` | `EmploymentCategoryEnum` | Enum: `{FULL_TIME, CONTRACT, INTERNSHIP, ACADEMIC_RESEARCH, OPEN_SOURCE}`. | Applies domain weighting modifiers (e.g., distinguishing full-time production ownership from internships). | 

#### Table 2.2.2: Extracted Skill Evidence & Project Complexity Schema

| Field Name | Conceptual Data Type | Constraints & Validation Rules | Domain Purpose & Downstream Usage | 
| ----- | ----- | ----- | ----- | 
| `SkillEvidenceRecords` | `Set<ExtractedSkillEvidence>` | Unique by `CanonicalSkillID`; $\ge 1$ required for valid technical CV. | Primary input for Dense/Sparse hybrid matching and Hard-Skill Gatekeeper verification. | 
| `SkillEvidence.CanonicalSkillID` | `TaxonomyNodeID` | Normalized via Domain Skill Ontology alias table (e.g., `"Golang"`, `"Go 1.22"` $\rightarrow$ `SKILL_LANG_GO`). | Eliminates lexical synonym misses during sparse keyword matching. | 
| `SkillEvidence.SkillCategory` | `SkillCategoryEnum` | Enum: `{PROGRAMMING_LANGUAGE, FRAMEWORK_LIBRARY, DATA_STORE, CLOUD_INFRA, ARCHITECTURE_PARADIGM, METHODOLOGY}`. | Groups competencies for structured rubric evaluation and UI Comparison Matrix rendering. | 
| `SkillEvidence.ClaimedProficiencyDepth` | `EvidentiaryDepthEnum` | Enum: `{LISTED_ONLY_NO_CONTEXT (1), ACADEMIC_OR_TOY_PROJECT (2), PRODUCTION_CONTRIBUTOR (3), PRODUCTION_ARCHITECT_OWNER (4)}`. | Prevents "Resume Skill Stuffing" (skills listed only in a footer skills block receive depth $= 1$ and trigger interview probes). | 
| `SkillEvidence.LinkedWorkEntryIDs` | `List<StringIdentifier>` | Must reference valid `WorkHistoryEntry.EntryID` or `ProjectEntry.ProjectID`; empty if `LISTED_ONLY_NO_CONTEXT`. | Enables computation of exact skill-specific recency $\Delta t_{\text{skill}}$ and cumulative applied duration $D_{\text{skill}}$. | 
| `SkillEvidence.VerbatimEvidenceQuote` | `BoundedString(Max=350)` | Must satisfy substring token overlap $\ge 0.90$ against source document tree. | Anti-hallucination invariant; displayed to recruiters in the UI as verifiable proof. | 
| `ProjectComplexityRecords` | `List<ExtractedProjectArtifact>` | Extracted for every distinct technical system/project described in the CV. | Seeds contextual deep-dive questions in the Phase 4 LangGraph interview. | 
| `ProjectArtifact.SystemScaleTier` | `ScaleComplexityEnum` | Enum: `{UNSPECIFIED, PROTOTYPE_SINGLE_USER, DEPARTMENTAL_INTERNAL, HIGH_THROUGHPUT_PRODUCTION, DISTRIBUTED_MISSION_CRITICAL}`. | Evaluates alignment with the target Job Requisition's operational scale. | 
| `ProjectArtifact.QuantifiedImpactMetrics` | `List<ImpactMetricTuple>` | Tuple of `(MetricType, NumericValue, Unit, VerbatimContext)` (e.g., `LATENCY_REDUCTION, 45, PERCENT`). | Separates outcome-driven engineers from task-list descriptors. | 
| `EpistemicGapSignals` | `List<EpistemicGapSignal>` | Generated whenever a claimed skill or project lacks architectural "How" or "Why" evidence. | **Critical Bridge to Phase 4:** Directly populates the initial `TargetProbeQueue` in the LangGraph interview state. | 
| `EpistemicGap.SuspectClaimSummary` | `BoundedString(Max=250)` | E.g., *"Claimed 'Designed high-availability RAG pipeline' but omitted vector DB, chunking strategy, and latency metrics."* | Instructs the LangGraph `QuestionGenerator` Node on exactly what technical claim to verify first. | 

### 2.3 Job Description (JD) Extraction & Rubric Contract: `JobCompetencyRubric`

When a Recruiter or Hiring Manager creates a `JobRequisition` by pasting a raw Job Description or configuring a structured template, the `JDCompetencyExtractionUseCase` parses and normalizes the requirements into an immutable `JobCompetencyRubric` Value Object.

#### Table 2.3.1: Job Description & Competency Rubric Conceptual Schema

| Field Name | Conceptual Data Type | Constraints & Validation Rules | Domain Purpose & Downstream Usage | 
| ----- | ----- | ----- | ----- | 
| `RequisitionID` | `UUIDv7` | Immutable aggregate identifier. | Scopes all candidate matching scores, vector filters, and interview sessions. | 
| `TargetSeniorityBand` | `SeniorityTierEnum` | Enum: `{JUNIOR_L1, MID_L2, SENIOR_L3, STAFF_PRINCIPAL_L4}`. | Determines required minimum months of experience $M_{\min}$ and expected rubric mastery levels. | 
| `HardSkillGatekeepers` | `Set<GatekeeperRule>` | Max $5$ gatekeepers per requisition to prevent over-constrained zero-recall filtering. | Binary or steep-penalty barrier criteria evaluated before weighted ranking. | 
| `GatekeeperRule.CanonicalSkillID` | `TaxonomyNodeID` | Must map to a valid node in the Domain Skill Ontology. | Identifies the non-negotiable technical requirement (e.g., `SKILL_LANG_PYTHON`). | 
| `GatekeeperRule.MinimumAppliedMonths` | `NonNegativeInteger` | Range $[0, 120]$; $0$ indicates conceptual familiarity is sufficient. | Verifies candidate has production tenure with the gatekeeper skill, not just a keyword mention. | 
| `GatekeeperRule.StrictDisqualificationPolicy` | `BooleanFlag` | If `TRUE`, failure caps total match score below the auto-invite threshold; if `FALSE`, applies soft penalty $\gamma_{\text{pen}}$. | Allows Hiring Managers to choose between strict knockout vs. heavy score penalization. | 
| `WeightedCompetencyDimensions` | `List<CompetencyDimension>` | Cardinality $K \in [3, 10]$; sum of weights must satisfy $\sum_{k=1}^{K} w_k = 1.000 \pm 10^{-4}$. | Defines the multi-dimensional evaluation space for both CV matching and live interviewing. | 
| `CompetencyDimension.DimensionID` | `UUIDv7` | Unique within the rubric. | Tracked across Phase 3 CV Matching, Phase 4 Live Interviewing, and Phase 7 Comparison Matrix. | 
| `CompetencyDimension.DimensionName` | `BoundedString(Max=80)` | E.g., *"Distributed Systems & Async Messaging"*, *"LLM Orchestration & RAG Architecture"*. | Human-readable competency title displayed on scorecards. | 
| `CompetencyDimension.PriorityClass` | `RequirementPriorityEnum` | Enum: `{MUST_HAVE_CORE, NICE_TO_HAVE_BONUS, DOMAIN_CONTEXT_PLUS}`. | Governs weight allocation bounds ($w_k \ge 0.15$ for `MUST_HAVE_CORE`, $w_k \le 0.08$ for `NICE_TO_HAVE_BONUS`). | 
| `CompetencyDimension.NormalizedWeight` | `DecimalFloat(4)` | $w_k \in (0.0, 1.0]$ such that $\sum w_k = 1.0$. | Scales the contribution of competency $k$ in the composite matching and interview scoring equations. | 
| `CompetencyDimension.TargetMasteryLevel` | `RubricDepthScale` | Integer scale $\{1, 2, 3, 4, 5\}$ ($1=$ Novice, $3=$ Independent Production Engineer, $5=$ Principal Authority). | Defines the stopping criterion and probing depth target for the Phase 4 LangGraph interview nodes. | 
| `CompetencyDimension.IdealAnchorDescriptor` | `BoundedText(Max=600)` | Concrete description of what a Level $5$ vs. Level $2$ technical profile/answer looks like for this competency. | Used as the semantic query anchor for dense vector retrieval AND as the grading rubric in Phase 4. | 

## 3. Chunking, Embedding & Vector Topology

### 3.1 Architecture Decision Record: ADR-004 (Chunking & Vector Granularity)

| ADR Field | Specification Details | 
| ----- | ----- | 
| **Identifier** | **ADR-004: Hierarchical Semantic Section + Parent-Child Multi-Vector Chunking Strategy** | 
| **Status** | Accepted & Enforced | 
| **Context** | Embedding an entire multi-page CV into a single dense vector ("Whole-Document Embedding") dilutes fine-grained technical evidence—causing a candidate with deep distributed systems expertise buried in one role to score poorly against a specific competency query. Conversely, naive fixed-size token window chunking (e.g., $256\text{-token}$ sliding windows) severs critical contextual boundaries, splitting a job's `RoleTitle` and `StartYearMonth` header from the bullet points describing the actual technical achievements several lines below. | 
| **Decision** | Adopt a **Hierarchical Semantic Section + Parent-Child Multi-Vector Strategy**:   1\. **Parent Chunks (Contextual Containers):** Each distinct `WorkHistoryEntry` or `ProjectArtifact` extracted in Stage 4 forms a self-contained Parent Chunk containing full role metadata (Title, Tenure Months, Recency $\Delta t$, Scale Tier) and complete bullet narratives.  2\. **Child Chunks (Atomic Evidence Units):** Each individual technical achievement, architectural decision, or skill application within a Parent Role is indexed as an atomic Child Vector ($64\text{--}180\text{ tokens}$), **prepended with a deterministic Contextual Header** inherited from its Parent (`"[Role: Senior Backend Engineer | Tenure: 28 mos | Recency: Active] ..."`).  3\. **Dual Sparse-Dense Representation:** Every Child Chunk is indexed with both a Dense Semantic Embedding vector ($($\mathbf{v}_{\text{dense}} \in \mathbb{R}^{$) and a Sparse Lexical Vector ($($\mathbf{v}_{\text{sparse$, BM25/SPLADE token weights) inside a single unified hybrid vector collection. | 
| **Alternatives Considered** | 1\. *Naive Fixed-Token Sliding Window:* Rejected due to context fragmentation and loss of role-tenure attribution.  2\. *Single Holistic CV Vector:* Rejected due to high cosine-similarity compression loss across multi-domain careers.  3\. *Late-Interaction Multi-Vector (ColBERT) Only:* Rejected due to $10\times\text{--}15\times$ higher memory footprint per 500-CV batch compared to Parent-Child contextualized hybrid chunks. | 
| **Consequences** | **Positive:** Preserves exact temporal and seniority attribution on every retrieved atomic achievement; enables per-competency maximum-evidence alignment rather than diluted document averages.  **Trade-offs:** Increases vector count per CV to $\approx 12\text{--}25$ child vectors (yielding $\approx 7,500\text{--}12,500$ vectors per 500-CV batch), which remains well within sub-$20\text{ ms}$ HNSW query latency when scoped by tenant and requisition payload filters. | 

### 3.2 Visual Architecture Diagram: Hierarchical Parent-Child Vector Topology

```
flowchart TD
    subgraph SourceDossier["Validated Candidate Dossier (Domain Value Object)"]
        Root["CandidateProfile Aggregate\n(CandidateID, TotalExperienceMonths, SeniorityTier)"]
        Role1["Parent Node P1: WorkHistoryEntry #1\n(Senior AI Engineer | 24 Months | Active: Δt = 0)"]
        Role2["Parent Node P2: WorkHistoryEntry #2\n(Backend Engineer | 18 Months | Historical: Δt = 24 mos)"]
        Proj1["Parent Node P3: OpenSource Project\n(Distributed Task Scheduler | Scale: Production)"]
    end

    subgraph ContextualizedChildChunks["Contextualized Atomic Child Chunks (Pre-Embedding)"]
        C1_1["Child C1.1: [Header P1] Architected stateful LangGraph multi-agent workflow reducing P95 latency by 38%"]
        C1_2["Child C1.2: [Header P1] Deployed hybrid Qdrant vector retrieval with BM25 RRF fusion over 10M chunks"]
        C2_1["Child C2.1: [Header P2] Built high-throughput FastAPI + PostgreSQL transactional outbox workers"]
        C3_1["Child C3.1: [Header P3] Implemented Raft-inspired leader election and dead-letter queue recovery"]
    end

    subgraph HybridVectorIndex["Multi-Tenant Hybrid Vector Store Collection"]
        DenseIdx["HNSW Dense Vector Index\n(Cosine Similarity, d = 1536 / 1024)"]
        SparseIdx["Inverted Sparse Lexical Index\n(BM25 / SPLADE Exact Technical Tokens)"]
        PayloadIdx["Indexed Payload Metadata\n(tenant_id, requisition_id, candidate_id, parent_id, skill_ids[], delta_t_months)"]
    end

    Root --> Role1
    Root --> Role2
    Root --> Proj1

    Role1 --> C1_1
    Role1 --> C1_2
    Role2 --> C2_1
    Proj1 --> C3_1

    C1_1 & C1_2 & C2_1 & C3_1 ==>|"Dual Encoding"| DenseIdx
    C1_1 & C1_2 & C2_1 & C3_1 ==>|"Lexical Weighting"| SparseIdx
    C1_1 & C1_2 & C2_1 & C3_1 ==>|"Pre-Filter Attributes"| PayloadIdx

```

### 3.3 Vector Index & Metadata Payload Specification

To guarantee strict multi-tenant isolation and sub-$15\text{ ms}$ hybrid retrieval during batch scoring, the Vector Store Adapter enforces the following collection schema and index parameters:

| Vector / Payload Attribute | Conceptual Type | Index Type & Configuration | Architectural Purpose | 
| ----- | ----- | ----- | ----- | 
| `PointID` | `UUIDv5` | Primary Key (`SHA1(TenantID + CandidateID + ChunkHash)`). | Guarantees idempotent upserts if a CV is re-indexed without creating duplicate vector points. | 
| `DenseEmbeddingVector` | `Float32Array[d]` | **HNSW Graph Index:** $M = 16$, $ef_{\text{construct}} = 200$, $ef_{\text{search}} = 128$, Metric $=$ `COSINE`, Scalar Quantization `INT8` in RAM with `FLOAT32` rescoring on disk. | Captures deep semantic equivalence across differently phrased architectural achievements. | 
| `SparseLexicalVector` | `SparseVector(Indices[], Weights[])` | **Inverted Index with Pruning:** Custom technical tokenizer preserving symbols (`C++`, `C#`, `.NET`, `Next.js`, `k8s`). | Captures exact framework versions, protocol acronyms, and proprietary tool names that dense embeddings smooth over. | 
| `Payload.tenant_id` | `UUIDv7` | **Tenant Partition Key (`is_tenant = true`)** + Co-located Shard Routing. | Enforces zero cross-tenant data leakage at the storage and HNSW subgraph level. | 
| `Payload.requisition_id` | `UUIDv7` | `KEYWORD` Exact Match Index. | Scopes batch candidate ranking queries exclusively to applicants within the active job requisition. | 
| `Payload.candidate_id` | `UUIDv7` | `KEYWORD` Exact Match Index. | Enables grouping Child Chunks back to their owning `CandidateProfile` during score aggregation. | 
| `Payload.parent_entry_id` | `StringIdentifier` | `KEYWORD` Exact Match Index. | Links atomic Child Chunks to their Parent `WorkHistoryEntry` or `ProjectArtifact`. | 
| `Payload.canonical_skill_ids` | `Array<TaxonomyNodeID>` | `KEYWORD` Multi-Value Array Index. | Supports fast boolean pre-filtering for Hard-Skill Gatekeeper queries. | 
| `Payload.recency_months_delta` | `NonNegativeInteger` | `INTEGER` Range Index ($\Delta t \in [0, 600]$). | Supplies $\Delta t$ directly during vector scoring to apply continuous exponential recency decay. | 
| `Payload.evidentiary_depth` | `Integer` | `INTEGER` Range Index ($\{1, 2, 3, 4\}$). | Enables down-weighting bare keyword lists (`depth = 1`) vs. production architecture ownership (`depth = 4`). | 

## 4. Hybrid Matching, Skill-Gap Analysis & Ranking Algorithm

### 4.1 Formal Mathematical Formulation of the Composite Match Score

Pure vector similarity between a resume and a Job Description is mathematically flawed for enterprise recruiting: it treats nice-to-have skills equally with mission-critical requirements, ignores whether a skill was used yesterday or ten years ago, and fails to enforce mandatory disqualifiers.

Therefore, the **Domain Layer** (`HybridMatchingDomainService`) computes a deterministic, explainable **Composite Candidate Match Score** $S_{\text{final}}(c, J) \in [0, 100]$ for candidate $c$ against Job Requisition $J$ across its $K$ weighted competency dimensions $\{D_1, D_2, \dots, D_K\}$.

#### Step 1: Temporal Recency Decay & Evidentiary Depth Multiplier per Evidence Chunk

For any candidate child chunk $m \in \mathcal{M}_c$ associated with a parent role whose end date is $\Delta t_m$ months prior to the evaluation date ($\Delta t_m = 0$ for current roles) and whose extracted evidentiary depth is $d_m \in \{1, 2, 3, 4\}$, the **Evidence Validity Weight** $\psi(m)$ is defined as:

$$
\psi(m) = \underbrace{\exp\left( -\lambda_k \cdot \frac{\Delta t_m}{12} \right)}_{\text{Exponential Recency Decay}} \times \underbrace{\left( 0.25 + 0.25 \cdot d_m \right)}_{\text{Evidentiary Depth Scaling } \in [0.50, 1.25]}
$$

Where:

* $\lambda_k = \frac{\ln(2)}{\tau_{1/2, k}}$ is the competency-specific decay constant governed by half-life $\tau_{1/2, k}$ in years:

  * For rapidly evolving frameworks/tools (`FRAMEWORK_LIBRARY`, e.g., LangChain, Next.js): $\tau_{1/2, k} = 3.0\text{ years}$ ($\lambda_k \approx 0.231$).

  * For foundational engineering paradigms (`ARCHITECTURE_PARADIGM`, algorithms, distributed systems): $\tau_{1/2, k} = 8.0\text{ years}$ ($\lambda_k \approx 0.0866$).

* Notice that a skill merely listed in a footer without project context ($d_m = 1$) receives a multiplier of $0.50$, whereas a skill demonstrated with production architectural ownership ($d_m = 4$) receives a $1.25$ boost (capped at $1.0$ after competency normalization).

#### Step 2: Per-Competency Dense Semantic & Sparse Lexical Alignment

For each competency dimension $D_k$ ($k \in \{1, \dots, K\}$) with dense query vector $\mathbf{q}_{k}^{\text{dense}}$ and sparse query vector $\mathbf{q}_{k}^{\text{sparse}}$, we evaluate all candidate chunks $m \in \mathcal{M}_c$:

1. **Dense Cosine Similarity Score** $s_{k, m}^{\text{dense}} \in [0, 1]$: 

   $$
   s_{k, m}^{\text{dense}} = \max\left( 0, \frac{\mathbf{q}_{k}^{\text{dense}} \cdot \mathbf{v}_{m}^{\text{dense}}}{\left\lVert \mathbf{q}_{k}^{\text{dense}} \right\rVert_2 \left\lVert \mathbf{v}_{m}^{\text{dense}} \right\rVert_2} \right)
   $$

2. **Normalized Sparse Lexical (BM25F) Score** $s_{k, m}^{\text{sparse}} \in [0, 1]$: 

   $$
   s_{k, m}^{\text{sparse}} = \frac{\text{BM25}\left(\mathbf{q}_{k}^{\text{sparse}}, \mathbf{v}_{m}^{\text{sparse}}\right)}{\text{BM25}_{\max}\left(\mathbf{q}_{k}^{\text{sparse}}\right) + \epsilon}
   $$

3. **Chunk-Level Hybrid Evidence Score** $e_{k, m}$: 

   $$
   e_{k, m} = \psi(m) \cdot \left( \alpha_k \cdot s_{k, m}^{\text{dense}} + (1 - \alpha_k) \cdot s_{k, m}^{\text{sparse}} \right)
   $$

    Where $\alpha_k \in [0, 1]$ controls the dense-vs-sparse balance (default $\alpha_k = 0.65$ for architectural competencies; $\alpha_k = 0.45$ for exact tool/language competencies).

#### Step 3: Top-$P$ Pooling with Diminishing Returns per Competency

Rather than taking only the single best chunk (which could be a lucky one-sentence match) or averaging all chunks (which penalizes long resumes), the candidate's mastery score $C_k(c) \in [0, 1]$ for competency $D_k$ aggregates the top $P = 3$ highest-scoring distinct chunks $e_{k, (1)} \ge e_{k, (2)} \ge e_{k, (3)}$ using geometrically decaying corroboration weights:

$$
C_k(c) = \min\left( 1.0, \sum_{p=1}^{P} \omega_p \cdot e_{k, (p)} \right), \quad \text{where } \omega_1 = 0.65, \quad \omega_2 = 0.25, \quad \omega_3 = 0.10
$$

This formulation requires corroborated evidence across multiple project achievements or roles to approach $C_k(c) \approx 1.0$.

#### Step 4: Hard-Skill Gatekeeper Barrier Function ($G_{\text{gate}}$)

Let $\mathcal{G} = \{g_1, \dots, g_H\}$ be the set of `HardSkillGatekeepers` defined on the Job Requisition. For each gatekeeper $g_h$ requiring skill $u_h$ with minimum applied duration $M_{h, \min}$ months, let $M_c(u_h)$ be the candidate's verified applied months in skill $u_h$. The individual gatekeeper satisfaction factor $\phi_h(c) \in [0, 1]$ is:

$$
\phi_h(c) = \begin{cases} 1.0 & \text{if } M_c(u_h) \ge M_{h, \min} \\ \left( \frac{M_c(u_h)}{M_{h, \min}} \right)^2 & \text{if } 0 < M_c(u_h) < M_{h, \min} \text{ and } \text{StrictPolicy} = \text{FALSE} \\ \gamma_{\text{missing}} & \text{if } M_c(u_h) = 0 \text{ and } \text{StrictPolicy} = \text{FALSE} \\ 0.0 & \text{if } M_c(u_h) < M_{h, \min} \text{ and } \text{StrictPolicy} = \text{TRUE} \end{cases}
$$

Where $\gamma_{\text{missing}} = 0.35$. The aggregate **Gatekeeper Multiplier** $G_{\text{gate}}(c)$ across all $H$ gatekeepers is:

$$
G_{\text{gate}}(c) = \prod_{h=1}^{H} \phi_h(c)
$$

*(Note: If any strict gatekeeper fails,* $G_{\text{gate}}(c) = 0$*, immediately routing the candidate to the `GATEKEEPER_DISQUALIFIED` tier while still preserving their raw competency breakdown* $C_k(c)$ *for recruiter auditability).*

#### Step 5: Seniority & Impact Calibration Factor ($\Phi_{\text{cal}}$)

To account for overall career trajectory alignment with the requisition's required experience $M_{\text{req}}$ (in months) and quantified impact evidence count $N_{\text{impact}}$, the calibration factor $\Phi_{\text{cal}}(c) \in [0.80, 1.10]$ is computed via a bounded asymmetric sigmoid:

$$
\Phi_{\text{cal}}(c) = \text{clamp}\left( 0.85 + 0.15 \cdot \sigma\left( \frac{M_{\text{total}}(c) - M_{\text{req}}}{12} \right) + 0.02 \cdot \min\left(N_{\text{impact}}, 5\right), \, 0.80, \, 1.10 \right)
$$

Where $\sigma(z) = \frac{1}{1 + e^{-z}}$.

#### Step 6: Final Composite Match Score ($S_{\text{final}}$)

Combining the weighted competency scores $\sum_{k=1}^{K} w_k C_k(c)$, the calibration factor $\Phi_{\text{cal}}(c)$, and the gatekeeper barrier $G_{\text{gate}}(c)$ yields the final normalized score on a $[0, 100]$ scale:

$$
S_{\text{final}}(c, J) = 100 \times G_{\text{gate}}(c) \times \min\left( 1.0, \, \Phi_{\text{cal}}(c) \cdot \sum_{k=1}^{K} w_k \cdot C_k(c) \right)
$$

### 4.2 Epistemic Skill-Gap Vector Synthesis (Bridge to Phase 4 LangGraph)

A core architectural innovation of this platform is that Phase 3 CV screening does not merely output a static scalar number—it computes a directional **Epistemic Skill-Gap Vector** $\mathbf{\Delta}(c, J) = \left[ \delta_1, \delta_2, \dots, \delta_K \right]$ that governs how the Phase 4 LangGraph Interview Engine allocates conversational time and question depth.

For each competency dimension $D_k$, the **Interview Probe Priority Score** $\Pi_k(c) \in [0, 1]$ is defined as:

$$
\Pi_k(c) = w_k \cdot \left( \beta_1 \cdot \underbrace{\left(1 - C_k(c)\right)}_{\text{Rubric Coverage Deficit}} + \beta_2 \cdot \underbrace{\mathbb{I}_{\text{vague}}(c, k)}_{\text{Unverified Claim Indicator}} \right)
$$

Where:

* $\beta_1 = 0.60$ weights the gap between the JD requirement and the candidate's demonstrated CV evidence.

* $\beta_2 = 0.40$ weights the presence of an `EpistemicGapSignal` $\mathbb{I}_{\text{vague}}(c, k) \in \{0, 1\}$ (i.e., the candidate claimed the skill on their CV with `ClaimedProficiencyDepth` $\le 2$ or made an unquantified architectural claim).

* The Phase 4 LangGraph `QuestionGenerator` Node sorts competencies in descending order of $\Pi_k(c)$ so the live interview spends maximum time probing high-weight competencies where the candidate's CV evidence is either missing or suspiciously vague.

### 4.3 Candidate Tiering Decision Matrix & Automated Workflow Routing

Once $S_{\text{final}}(c, J)$ and the individual competency scores $C_k(c)$ are computed, the `CandidateTieringPolicy` Domain Service deterministically classifies the candidate into one of four operational tiers:

| Candidate Tier Classification | Mathematical Decision Condition | Automated System Action & Domain Event | Recruiter Dashboard Presentation | 
| ----- | ----- | ----- | ----- | 
| **Tier 1: `AUTO_INVITE_INTERVIEW`** | $G_{\text{gate}}(c) = 1.0$ **AND** $S_{\text{final}}(c, J) \ge \theta_{\text{invite}}$ (Default $\theta_{\text{invite}} = 75.0$) **AND** $\forall k \in \mathcal{K}_{\text{must}}, C_k(c) \ge 0.50$. | Automatically provisions an `InterviewSession` aggregate seeded with $\mathbf{\Delta}(c, J)$ and dispatches `CandidateInvitedToInterviewEvent` (or queues for 1-click batch approval if human-in-the-loop mode is enabled). | Highlighted in **Emerald Priority Queue** with full evidence citations and scheduled interview link status. | 
| **Tier 2: `BORDERLINE_HUMAN_REVIEW`** | $G_{\text{gate}}(c) \ge 0.60$ **AND** $\theta_{\text{review}} \le S_{\text{final}}(c, J) < \theta_{\text{invite}}$ (Default $\theta_{\text{review}} = 58.0$) **OR** ($S_{\text{final}} \ge 75.0$ but one `MUST_HAVE_CORE` dimension has $0.35 \le C_k(c) < 0.50$). | Places candidate in `AWAITING_RECRUITER_TRIAGE` state; generates a 3-bullet `TriageJustificationSummary` highlighting the exact missing competency vs. strong compensating signals. | Displayed in **Amber Review Queue** with 1-click actions: *"Override & Invite to AI Interview"* or *"Reject"*. | 
| **Tier 3: `SILVER_MEDALIST_TALENT_POOL`** | $G_{\text{gate}}(c) = 0.0$ (failed a specific tool/tenure gatekeeper) **AND** Raw Weighted Score $\sum w_k C_k(c) \ge 0.72$. | Retains candidate in the cross-requisition `TalentPool` index; emits `SilverMedalistIdentifiedEvent` suggesting alternative open requisitions where the candidate satisfies all gatekeepers. | Displayed in **Cross-Role Match Suggestions** tab. | 
| **Tier 4: `AUTOMATED_REJECTION`** | $S_{\text{final}}(c, J) < \theta_{\text{review}}$ ($58.0$) **AND** Raw Weighted Score $\sum w_k C_k(c) < 0.72$. | Transitions candidate to `SCREENING_REJECTED`; records immutable audit log of exact rubric deficits and gatekeeper calculations for compliance reporting. | Collapsed in **Archived / Below Threshold** table with full mathematical score breakdown inspectable on demand. | 

### 4.4 Outbound Application Port Specification: `HybridSemanticMatcherPort`

To keep the `EvaluateCandidateJobMatchUseCase` and `HybridMatchingDomainService` strictly separated from Qdrant/pgvector client libraries and BM25 tokenizers, retrieval of raw similarity matrices is abstracted behind `HybridSemanticMatcherPort`:

| Port Contract Element | Specification Details | 
| ----- | ----- | 
| **Port Interface Name** | `HybridSemanticMatcherPort` (Resides in `backend/src/modules/document_intelligence/application/ports/outbound/`) | 
| **Method Signature** | `RetrieveCompetencyEvidenceMatrix(TenantID, RequisitionID, CandidateID, List<CompetencyQueryDescriptor>) -> CompetencyEvidenceMatrixDTO` | 
| **Input Preconditions** | 1\. Candidate chunks for `(TenantID, RequisitionID, CandidateID)` have already been indexed in Stage 5.  2\. Each `CompetencyQueryDescriptor` contains the `DimensionID`, `IdealAnchorDescriptor` text, and target `CanonicalSkillIDs`. | 
| **Infrastructure Adapter Execution (`QdrantHybridMatcherAdapter`)** | 1\. Encodes all `K` competency `IdealAnchorDescriptor` strings into dense and sparse query vectors in a single batched inference call.  2\. Executes a single multi-search batch request against the vector store filtered strictly by `tenant_id`, `requisition_id`, and `candidate_id`.  3\. Returns the top $P = 5$ candidate child chunks per competency along with their raw $s_{k,m}^{\text{dense}}$, $s_{k,m}^{\text{sparse}}$, $\Delta t_m$, and $d_m$ metadata attributes. | 
| **Output Postconditions** | Returns a pure `CompetencyEvidenceMatrixDTO` containing zero vector database SDK objects; the **Domain Layer** (`HybridMatchingDomainService`) then executes Steps 1 through 6 of the mathematical model in pure in-memory domain logic. | 
| **Failure Modes** | `CandidateVectorsNotFoundError`, `VectorIndexTimeoutError`, `EmbeddingServiceUnavailableError`. | 
