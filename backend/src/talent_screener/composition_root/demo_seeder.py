"""Idempotent Interactive Demo Environment Seeder & Live Batch Simulator.

Provisions a full realistic hiring scenario through genuine Clean Architecture Use Cases:
- Demo Tenant ('Acme AI Engineering') + GDPR Consents + Pre-Signed Recruiter/Candidate JWTs
- Published 'Senior AI Systems & Full-Stack Engineer' JobRequisition (4 competencies, 2 gatekeepers)
- 4-CV ScreeningBatch -> PII Vault -> INT8 Vector Store -> Transactional Outbox -> Hybrid Matching
- 3 Completed Adaptive Interviews & Finalized EvaluationReports (L2..L5 varied depths)
- 1 Active IN_PROGRESS InterviewSession sitting at Turn 1 ready for WebSocket/REST interaction
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from talent_screener.application.document_intelligence.dtos import (
    BatchFileUploadSpecDTO,
    ExecuteHybridMatchingRequestDTO,
    InitiateScreeningBatchRequestDTO,
    ProcessDocumentIngestionTaskRequestDTO,
)
from talent_screener.application.identity_governance.dtos import (
    ProvisionTenantAccountRequestDTO,
    RecordCandidateConsentRequestDTO,
)
from talent_screener.application.live_interview.dtos import (
    ClientTelemetryPayloadDTO,
    CompleteInterviewSessionRequestDTO,
    InitializeInterviewSessionRequestDTO,
    ProcessCandidateTurnRequestDTO,
)
from talent_screener.application.recruitment.dtos import (
    CompetencyCriterionInputDTO,
    CreateJobRequisitionRequestDTO,
    PublishJobRequisitionRequestDTO,
    TierThresholdsDTO,
)
from talent_screener.domain.contexts.document_intelligence import CandidateTier
from talent_screener.domain.contexts.identity_governance import ConsentScope
from talent_screener.domain.contexts.recruitment import SeniorityLevel
from talent_screener.domain.shared import SHA256Hash, TenantId
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.presentation.middleware.auth import RbacScope

if TYPE_CHECKING:
    from talent_screener.composition_root.container import Phase8PipelineBundle

_DEMO_SEED_KEY = "acme_ai_demo_v1"
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()

_ALL_RECRUITER_SCOPES: tuple[str, ...] = (
    RbacScope.RECRUITMENT_READ,
    RbacScope.RECRUITMENT_WRITE,
    RbacScope.SCREENING_READ,
    RbacScope.SCREENING_WRITE,
    RbacScope.INTERVIEW_MANAGE,
    RbacScope.INTERVIEW_CANDIDATE,
    RbacScope.EVALUATION_READ,
    RbacScope.EVALUATION_OVERRIDE,
    RbacScope.GOVERNANCE_ADMIN,
)

_CANDIDATE_SCOPES: tuple[str, ...] = (RbacScope.INTERVIEW_CANDIDATE,)

_DEMO_CVS: tuple[tuple[str, str, bytes, str], ...] = (
    (
        "Elena Vance",
        "elena_vance_staff_ai.pdf",
        (
            b"%PDF-1.7\n"
            b"Name: Elena Vance\n"
            b"Email: elena.vance@acme-demo.dev\n"
            b"EXPERIENCE\n"
            b"Staff AI Platform Architect 2017-03 to Present\n"
            b"Architected Python asyncio FastAPI distributed concurrency gateways at 120k rps "
            b"with 45% latency reduction.\n"
            b"PROJECTS\n"
            b"Architected LangGraph LangChain LLM RAG interview agent workflows scaling 90k rps "
            b"with 60% accuracy improvement.\n"
            b"EXPERIENCE\n"
            b"Principal Retrieval Architect 2019-01 to 2022-06\n"
            b"Architected Qdrant pgvector vector embeddings BM25 retrieval clusters handling "
            b"80k rps with 40% memory reduction.\n"
            b"PROJECTS\n"
            b"Architected TypeScript React Next.js portals scaling 50k rps, 42% faster render.\n"
        ),
        "shortlisted",
    ),
    (
        "Marcus Chen",
        "marcus_chen_senior_fs.pdf",
        (
            b"%PDF-1.7\n"
            b"Name: Marcus Chen\n"
            b"Email: marcus.chen@acme-demo.dev\n"
            b"EXPERIENCE\n"
            b"Senior Backend & AI Engineer 2019-05 to Present\n"
            b"Built Python asyncio FastAPI distributed microservices scaling 40k rps with 32% "
            b"cost reduction.\n"
            b"PROJECTS\n"
            b"Built LangGraph LangChain LLM RAG evaluation pipelines with 25% accuracy gain.\n"
            b"EXPERIENCE\n"
            b"Full-Stack Platform Engineer 2018-01 to 2019-04\n"
            b"Built Qdrant pgvector vector BM25 services and TypeScript React Next.js dashboards "
            b"serving 20k rps with 30% faster queries.\n"
        ),
        "shortlisted",
    ),
    (
        "Priya Nair",
        "priya_nair_mid_ai.pdf",
        (
            b"%PDF-1.7\n"
            b"Name: Priya Nair\n"
            b"Email: priya.nair@acme-demo.dev\n"
            b"EXPERIENCE\n"
            b"Software Engineer 2022-01 to Present\n"
            b"Built Python asyncio FastAPI endpoints and TypeScript React Next.js UI workflows.\n"
            b"PROJECTS\n"
            b"Built LangGraph LLM RAG prototypes and Qdrant pgvector vector search indexing.\n"
        ),
        "shortlisted",
    ),
    (
        "Devon Brooks",
        "devon_brooks_systems_go.pdf",
        (
            b"%PDF-1.7\n"
            b"Name: Devon Brooks\n"
            b"Email: devon.brooks@acme-demo.dev\n"
            b"SUMMARY\n"
            b"Principal Systems Engineer focused on Golang, Rust, Kubernetes, Docker, PostgreSQL.\n"
            b"EXPERIENCE\n"
            b"Principal Systems Engineer 2018-01 to Present\n"
            b"Engineered Raft storage daemons in Rust and Golang orchestrators on Kubernetes with "
            b"210k rps throughput and 38% memory reduction.\n"
        ),
        "gatekeeper_rejected",
    ),
)


@dataclass(frozen=True, slots=True)
class DemoSeedManifest:
    """Complete seeded demo identifiers, candidate metadata & pre-signed development JWTs."""

    tenant_id: str
    tenant_slug: str
    organization_name: str
    requisition_id: str
    requisition_title: str
    batch_id: str
    candidate_profile_ids: tuple[str, ...]
    shortlisted_candidate_ids: tuple[str, ...]
    rejected_candidate_id: str | None
    completed_session_ids: tuple[str, ...]
    active_session_id: str
    active_session_candidate_id: str
    active_session_turn_number: int
    active_session_difficulty: int
    active_session_question: str
    active_session_checkpoint_version: str
    report_ids: tuple[str, ...]
    candidate_summaries: tuple[dict[str, object], ...]
    recruiter_jwt: str
    candidate_jwt: str
    llm_mode: str = "deterministic"
    llm_provider: str = "deterministic"
    llm_model: str = "deterministic-offline-v1"
    seeded_at: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["candidate_profile_ids"] = list(self.candidate_profile_ids)
        payload["shortlisted_candidate_ids"] = list(self.shortlisted_candidate_ids)
        payload["completed_session_ids"] = list(self.completed_session_ids)
        payload["report_ids"] = list(self.report_ids)
        payload["candidate_summaries"] = list(self.candidate_summaries)
        return payload


def _clean_telemetry(latency_ms: int = 1200, wpm: float = 138.0) -> ClientTelemetryPayloadDTO:
    return ClientTelemetryPayloadDTO(
        focus_loss_count=0,
        focus_loss_duration_ms=0,
        external_paste_detected=False,
        paste_char_count=0,
        keystroke_anomaly_detected=False,
        response_latency_ms=latency_ms,
        speech_cadence_wpm=wpm,
        pause_entropy=0.28,
    )


def _ensure_seed_table(bundle: Phase8PipelineBundle) -> None:
    with bundle.db_manager.connection:
        bundle.db_manager.execute(
            """
            CREATE TABLE IF NOT EXISTS demo_seed_state (
                seed_key TEXT PRIMARY KEY,
                manifest_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def _issue_demo_tokens(
    bundle: Phase8PipelineBundle,
    *,
    tenant_id: TenantId,
    candidate_actor_id: UUID,
) -> tuple[str, str]:
    recruiter_jwt = bundle.authenticator.issue_token(
        tenant_id=tenant_id,
        actor_id=UUID("01930000-0000-7000-8000-000000000001"),
        scopes=_ALL_RECRUITER_SCOPES,
        ttl_seconds=86400 * 7,
    )
    candidate_jwt = bundle.authenticator.issue_token(
        tenant_id=tenant_id,
        actor_id=candidate_actor_id,
        scopes=_CANDIDATE_SCOPES,
        ttl_seconds=86400 * 7,
    )
    return recruiter_jwt, candidate_jwt


def _record_baseline_ops_telemetry(
    bundle: Phase8PipelineBundle,
    *,
    tenant_id: TenantId,
    requisition_id: UUID,
    session_ids: Sequence[UUID] | list[UUID] | tuple[UUID, ...],
) -> None:
    if bundle.finops_controller.summarize_ledger().get("invocation_count", 0) == 0:
        for idx, sid in enumerate(session_ids):
            bundle.finops_controller.record_usage(
                tenant_id=tenant_id,
                requisition_id=requisition_id,
                session_id=sid,
                node_name="cognitive_synthesis",
                cached_input_tokens=620,
                uncached_input_tokens=940 + idx * 120,
                output_tokens=310 + idx * 45,
            )
    if not bundle.rubric_drift_monitor.summarize().get("psi_assessments"):
        bundle.rubric_drift_monitor.evaluate_psi(
            competency_label="SKILL_LANG_PYTHON",
            baseline_distribution=[0.10, 0.20, 0.40, 0.20, 0.10],
            current_distribution=[0.11, 0.19, 0.39, 0.21, 0.10],
        )
        bundle.rubric_drift_monitor.evaluate_kappa(
            label="senior_ai_panel_alignment",
            ai_verdicts=["STRONG_HIRE", "HIRE", "BORDERLINE_HOLD", "HIRE"],
            reference_verdicts=["STRONG_HIRE", "HIRE", "BORDERLINE_HOLD", "HIRE"],
        )


def _llm_runtime_summary(bundle: Phase8PipelineBundle) -> tuple[str, str, str]:
    return bundle.llm_runtime_metadata()


async def seed_interactive_demo_environment(
    bundle: Phase8PipelineBundle,
) -> DemoSeedManifest:
    """Idempotently populate SQLite, PII Vault, INT8 Vector Index, Interviews & Reports."""
    _ensure_seed_table(bundle)
    llm_mode, llm_provider, llm_model = _llm_runtime_summary(bundle)

    existing_row = bundle.db_manager.fetchone(
        "SELECT manifest_json FROM demo_seed_state WHERE seed_key = ?",
        (_DEMO_SEED_KEY,),
    )
    if existing_row is not None:
        raw = json.loads(str(existing_row["manifest_json"]))
        tid = TenantId.from_str(str(raw["tenant_id"]))
        req_uuid = UUID(str(raw["requisition_id"]))
        active_cand_uuid = UUID(str(raw["active_session_candidate_id"]))

        # Rebuild in-memory quantized index & warm FinOps/Drift baseline if restarted
        await bundle.reconciler.reconcile_tenant(tid, default_requisition_id=req_uuid)
        _record_baseline_ops_telemetry(
            bundle,
            tenant_id=tid,
            requisition_id=req_uuid,
            session_ids=tuple(UUID(str(x)) for x in raw["completed_session_ids"]),
        )
        recruiter_jwt, candidate_jwt = _issue_demo_tokens(
            bundle,
            tenant_id=tid,
            candidate_actor_id=active_cand_uuid,
        )
        return DemoSeedManifest(
            tenant_id=str(raw["tenant_id"]),
            tenant_slug=str(raw["tenant_slug"]),
            organization_name=str(raw["organization_name"]),
            requisition_id=str(raw["requisition_id"]),
            requisition_title=str(raw["requisition_title"]),
            batch_id=str(raw["batch_id"]),
            candidate_profile_ids=tuple(str(x) for x in raw["candidate_profile_ids"]),
            shortlisted_candidate_ids=tuple(str(x) for x in raw["shortlisted_candidate_ids"]),
            rejected_candidate_id=(
                str(raw["rejected_candidate_id"])
                if raw.get("rejected_candidate_id") is not None
                else None
            ),
            completed_session_ids=tuple(str(x) for x in raw["completed_session_ids"]),
            active_session_id=str(raw["active_session_id"]),
            active_session_candidate_id=str(raw["active_session_candidate_id"]),
            active_session_turn_number=int(raw["active_session_turn_number"]),
            active_session_difficulty=int(raw["active_session_difficulty"]),
            active_session_question=str(raw["active_session_question"]),
            active_session_checkpoint_version=str(raw["active_session_checkpoint_version"]),
            report_ids=tuple(str(x) for x in raw["report_ids"]),
            candidate_summaries=tuple(dict(item) for item in raw["candidate_summaries"]),
            recruiter_jwt=recruiter_jwt,
            candidate_jwt=candidate_jwt,
            llm_mode=llm_mode,
            llm_provider=llm_provider,
            llm_model=llm_model,
            seeded_at=str(raw.get("seeded_at", utc_now().isoformat())),
        )

    c = bundle.container

    # 1. Provision Demo Tenant ('Acme AI Engineering')
    tenant_res = await c.provision_tenant_account.execute(
        ProvisionTenantAccountRequestDTO(
            organization_slug="acme-ai-engineering",
            display_name="Acme AI Engineering",
            max_files_per_batch=100,
        )
    )
    tid = tenant_res.tenant_id

    # 2. Create & Publish Requisition ('Senior AI Systems & Full-Stack Engineer')
    req_title = "Senior AI Systems & Full-Stack Engineer"
    req_res = await c.create_job_requisition.execute(
        CreateJobRequisitionRequestDTO(
            tenant_id=tid,
            title=req_title,
            description=(
                "Lead architecture across distributed Python asyncio/FastAPI workers, stateful "
                "LangGraph AI interview agents, INT8 hybrid vector & SQL persistence, and "
                "Next.js Clean Architecture real-time portals."
            ),
            seniority_level=SeniorityLevel.SENIOR,
            tier_thresholds=TierThresholdsDTO(
                fast_track_threshold=88.0,
                interview_threshold=65.0,
                review_threshold=50.0,
            ),
            criteria=(
                CompetencyCriterionInputDTO(
                    name="Distributed Backend & FastAPI",
                    canonical_skill_id="SKILL_LANG_PYTHON",
                    weight=0.30,
                    target_depth=4,
                    is_gatekeeper=True,
                    min_applied_months=24,
                    ideal_anchor_descriptor="Python asyncio FastAPI distributed concurrency",
                ),
                CompetencyCriterionInputDTO(
                    name="Stateful LangGraph AI Agents",
                    canonical_skill_id="SKILL_LANGGRAPH",
                    weight=0.30,
                    target_depth=4,
                    is_gatekeeper=False,
                    min_applied_months=12,
                    ideal_anchor_descriptor="LangGraph LangChain LLM RAG",
                ),
                CompetencyCriterionInputDTO(
                    name="Hybrid Vector & SQL Persistence",
                    canonical_skill_id="SKILL_VECTOR_SEARCH",
                    weight=0.20,
                    target_depth=4,
                    is_gatekeeper=False,
                    min_applied_months=12,
                    ideal_anchor_descriptor="Qdrant pgvector vector embeddings BM25",
                ),
                CompetencyCriterionInputDTO(
                    name="Next.js Clean Architecture",
                    canonical_skill_id="SKILL_TYPESCRIPT",
                    weight=0.20,
                    target_depth=4,
                    is_gatekeeper=True,
                    min_applied_months=18,
                    ideal_anchor_descriptor="TypeScript React Next.js",
                ),
            ),
        )
    )
    await c.publish_job_requisition.execute(
        PublishJobRequisitionRequestDTO(tenant_id=tid, requisition_id=req_res.requisition_id)
    )

    # 3. Ingest 4 CVs through ScreeningBatch -> Scanner -> Parser -> PII Vault -> Vector Index
    specs = tuple(
        BatchFileUploadSpecDTO(
            filename=filename,
            sha256_hex=SHA256Hash.from_bytes(cv_bytes).value,
            file_size_bytes=len(cv_bytes),
            mime_type="application/pdf",
        )
        for _name, filename, cv_bytes, _role in _DEMO_CVS
    )
    batch_init = await c.initiate_screening_batch.execute(
        InitiateScreeningBatchRequestDTO(
            tenant_id=tid,
            requisition_id=req_res.requisition_id,
            batch_label="acme-seed-wave-1",
            files=specs,
            idempotency_key="demo-seed-batch-v1",
            correlation_id="corr-demo-seed-batch",
        )
    )

    candidate_ids: list[UUID] = []
    shortlisted_ids: list[UUID] = []
    rejected_id: UUID | None = None
    candidate_names_by_id: dict[UUID, str] = {}
    candidate_files_by_id: dict[UUID, str] = {}
    match_outcomes: dict[UUID, tuple[float, CandidateTier]] = {}

    for (cand_name, filename, cv_bytes, _expectation), instr in zip(
        _DEMO_CVS, batch_init.upload_instructions, strict=True
    ):
        await bundle.adapters.object_storage.put_bytes(
            tenant_id=tid,
            object_key=instr.object_key,
            payload=cv_bytes,
            content_type="application/pdf",
        )
        ingested = await c.process_document_task.execute(
            ProcessDocumentIngestionTaskRequestDTO(
                tenant_id=tid,
                batch_id=batch_init.batch_id,
                task_id=instr.task_id,
                object_key=instr.object_key,
                correlation_id=f"corr-seed-{filename}",
            )
        )
        assert ingested.candidate_profile_id is not None
        cid = ingested.candidate_profile_id
        candidate_ids.append(cid)
        candidate_names_by_id[cid] = cand_name
        candidate_files_by_id[cid] = filename

        await c.record_candidate_consent.execute(
            RecordCandidateConsentRequestDTO(
                tenant_id=tid,
                candidate_id=cid,
                granted_scopes=(
                    ConsentScope.AI_SCREENING,
                    ConsentScope.VOICE_INTERVIEW_RECORDING,
                    ConsentScope.INTEGRITY_TELEMETRY,
                ),
            )
        )

    await bundle.outbox_relay.poll_and_dispatch(tenant_id=tid)

    for cid in candidate_ids:
        match_res = await c.execute_hybrid_matching.execute(
            ExecuteHybridMatchingRequestDTO(
                tenant_id=tid,
                requisition_id=req_res.requisition_id,
                candidate_profile_id=cid,
            )
        )
        match_outcomes[cid] = (round(match_res.composite_score, 4), match_res.tier)
        if match_res.tier is CandidateTier.GATEKEEPER_REJECTED:
            rejected_id = cid
        else:
            shortlisted_ids.append(cid)

    # 4. Run 3 completed adaptive interviews across all 4 competencies (L2..L5 differentiation)
    turn_scripts: tuple[tuple[str, ...], ...] = (
        (
            "For Python asyncio and FastAPI distributed concurrency, we bound worker pools via "
            "asyncio Semaphore, monotonic fencing lease tokens, and SQL WAL transactional outbox "
            "commits to prevent split-brain writes, trading 4ms overhead for 120k rps at 38ms p99.",
            "Under L4/L5 distributed failover, quorum fencing tokens reject stale leases, circuit "
            "breakers apply exponential backoff jitter, and backpressure queues cap memory "
            "bottleneck tradeoffs with 99.9% throughput stability under burst load.",
            "On TypeScript React and Next.js Clean Architecture, we isolate WebSocket frames and "
            "ring buffer AudioWorklet hysteresis in infrastructure behind a 13-state FSM "
            "coordinator, trading reducer overhead for predictable 280ms latency.",
            "For TypeScript React and Next.js SSE monitors, requestAnimationFrame batch "
            "coalescing caps DOM layout thrashing and memory churn bottleneck tradeoffs, "
            "maintaining 60fps UI updates across 500 concurrent tasks.",
            "In LangGraph LangChain LLM RAG agents, we checkpoint typed reducer planes with lock "
            "fencing and XML isolation boundaries to defeat prompt injection latency and "
            "throughput tradeoffs, achieving 45% token cost savings at 90k rps.",
            "During LangGraph L4 Socratic escalation, deterministic edge routing bounds retry "
            "loops with sliding window token budgets and fallback guards so hallucination risk "
            "stays under 2% while preserving 180ms median turn latency.",
            "For Qdrant pgvector vector embeddings and BM25 persistence, INT8 quantization "
            "with anti-entropy SQL outbox watermark reconciliation cuts RAM overhead 4x with 99% "
            "recall, trading minimal precision loss for linear shard scaling.",
            "Across L4 vector index shards, consistent hashing routes tenant partitions with LRU "
            "cache TTL eviction and idempotent upserts so outbox replays prevent skew.",
        ),
        (
            "We pair Python asyncio FastAPI distributed workers with bounded semaphore queues and "
            "idempotency keys in SQL outbox tables, handling backpressure via retry backoff "
            "jitter and timeout circuit breakers at 40k rps with 32% lower memory overhead.",
            "At L4 Python asyncio overload, mutex lease timeouts and queue batch size limits cap "
            "downstream bottleneck risk, trading slightly higher p99 latency for 99.5% durability.",
            "On TypeScript React Next.js portals, we isolate WebSocket state and async telemetry "
            "hooks behind typed ports, trading extra abstraction overhead for predictable "
            "reconnect recovery and 30% lower memory usage.",
            "For Next.js TypeScript state sync, LRU memoization and backoff reconnect jitter keep "
            "tail UI jitter below 45ms under bursty socket churn.",
            "With LangGraph LangChain LLM RAG agents, sliding token budgets and WAL checkpointing "
            "prevent stale conversation offset drift, balancing latency vs accuracy tradeoffs.",
            "During LangGraph L4 Socratic probing, deterministic edge guards enforce retry bounds "
            "and JSON schema validation fallback with 25% lower token cost.",
            "Using Qdrant pgvector vector embeddings and BM25 search, SQL watermark reconciliation "
            "and TTL cache eviction prevent stale shard drift at 20k rps.",
            "Across pgvector L4 partitions, consistent index sharding bounds query p99 latency "
            "under 35ms while trading small INT8 quantization overhead for 3x RAM savings.",
        ),
        (
            "We used Python and FastAPI microservices behind a load balancer with caching.",
            "On Next.js TypeScript pages we fetch endpoints and update component hooks.",
            "We built LangGraph and RAG chains that query Qdrant vectors directly.",
            "When errors happen we retry database writes with queue workers.",
        ),
    )

    completed_session_ids: list[UUID] = []
    report_ids: list[UUID] = []
    report_by_cand: dict[UUID, tuple[UUID, str]] = {}

    for idx, cid in enumerate(shortlisted_ids[:3]):
        init_iv = await c.initialize_interview_session.execute(
            InitializeInterviewSessionRequestDTO(
                tenant_id=tid,
                requisition_id=req_res.requisition_id,
                candidate_profile_id=cid,
                max_turns=10,
            )
        )
        ckpt = init_iv.checkpoint_version
        already_completed = False
        for t_idx, utterance in enumerate(turn_scripts[idx], start=1):
            t_res = await c.process_candidate_turn.execute(
                ProcessCandidateTurnRequestDTO(
                    tenant_id=tid,
                    session_id=init_iv.session_id,
                    turn_number=t_idx,
                    checkpoint_version=ckpt,
                    candidate_utterance=utterance,
                    remaining_seconds=max(300, 1750 - t_idx * 160),
                    telemetry=_clean_telemetry(
                        920 if idx == 0 else 1320 + idx * 180,
                        wpm=145.0 - idx * 8.0,
                    ),
                    correlation_id=f"corr-seed-c{idx}-t{t_idx}",
                )
            )
            ckpt = t_res.updated_checkpoint_version
            already_completed = t_res.is_completed
            if already_completed:
                break

        if not already_completed:
            await c.complete_interview_session.execute(
                CompleteInterviewSessionRequestDTO(
                    tenant_id=tid,
                    session_id=init_iv.session_id,
                    checkpoint_version=ckpt,
                )
            )
        completed_session_ids.append(init_iv.session_id)
        report = bundle.repositories.report_repo.get_by_session_id(tid, init_iv.session_id)
        if report is not None:
            report_ids.append(report.id)
            report_by_cand[cid] = (report.id, report.effective_recommendation_band.value)

    _record_baseline_ops_telemetry(
        bundle,
        tenant_id=tid,
        requisition_id=req_res.requisition_id,
        session_ids=tuple(completed_session_ids),
    )

    # 5. Provision 1 Active IN_PROGRESS InterviewSession at Turn 1 ready for candidate UI
    active_candidate = shortlisted_ids[0] if shortlisted_ids else candidate_ids[0]
    active_iv = await c.initialize_interview_session.execute(
        InitializeInterviewSessionRequestDTO(
            tenant_id=tid,
            requisition_id=req_res.requisition_id,
            candidate_profile_id=active_candidate,
            max_turns=6,
        )
    )

    summaries_list: list[dict[str, object]] = []
    for cid in candidate_ids:
        summaries_list.append(
            {
                "candidate_profile_id": str(cid),
                "display_name": candidate_names_by_id[cid],
                "filename": candidate_files_by_id[cid],
                "match_score": match_outcomes[cid][0],
                "tier": match_outcomes[cid][1].value,
                "report_id": str(report_by_cand[cid][0]) if cid in report_by_cand else None,
                "recommendation_band": report_by_cand[cid][1] if cid in report_by_cand else None,
            }
        )
    summaries = tuple(summaries_list)

    recruiter_jwt, candidate_jwt = _issue_demo_tokens(
        bundle,
        tenant_id=tid,
        candidate_actor_id=active_candidate,
    )

    now_iso = utc_now().isoformat()
    manifest = DemoSeedManifest(
        tenant_id=str(tid.value),
        tenant_slug="acme-ai-engineering",
        organization_name="Acme AI Engineering",
        requisition_id=str(req_res.requisition_id),
        requisition_title=req_title,
        batch_id=str(batch_init.batch_id),
        candidate_profile_ids=tuple(str(cid) for cid in candidate_ids),
        shortlisted_candidate_ids=tuple(str(cid) for cid in shortlisted_ids),
        rejected_candidate_id=str(rejected_id) if rejected_id is not None else None,
        completed_session_ids=tuple(str(sid) for sid in completed_session_ids),
        active_session_id=str(active_iv.session_id),
        active_session_candidate_id=str(active_candidate),
        active_session_turn_number=1,
        active_session_difficulty=int(active_iv.initial_difficulty),
        active_session_question=active_iv.opening_question,
        active_session_checkpoint_version=active_iv.checkpoint_version,
        report_ids=tuple(str(rid) for rid in report_ids),
        candidate_summaries=summaries,
        recruiter_jwt=recruiter_jwt,
        candidate_jwt=candidate_jwt,
        llm_mode=llm_mode,
        llm_provider=llm_provider,
        llm_model=llm_model,
        seeded_at=now_iso,
    )

    with bundle.db_manager.connection:
        bundle.db_manager.execute(
            """
            INSERT INTO demo_seed_state (seed_key, manifest_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(seed_key) DO UPDATE SET
                manifest_json = excluded.manifest_json,
                updated_at = excluded.updated_at
            """,
            (
                _DEMO_SEED_KEY,
                json.dumps(
                    {k: v for k, v in manifest.to_dict().items() if not k.endswith("_jwt")}
                ),
                datetime.fromisoformat(now_iso).isoformat(),
            ),
        )

    return manifest


async def simulate_live_demo_batch(bundle: Phase8PipelineBundle) -> Mapping[str, object]:
    """Initiate a fresh simulated 2-CV batch and stream its ingestion in the background."""
    manifest = await seed_interactive_demo_environment(bundle)
    tid = TenantId.from_str(manifest.tenant_id)
    req_uuid = UUID(manifest.requisition_id)
    run_id = str(generate_uuid7())[:8]

    sim_cvs: tuple[tuple[str, bytes], ...] = (
        (
            f"aria_kim_{run_id}.pdf",
            (
                b"%PDF-1.7\nName: Aria Kim\nEmail: aria.kim@acme-demo.dev\nSUMMARY\n"
                b"Senior Python FastAPI LangGraph TypeScript Next.js engineer.\n"
                b"EXPERIENCE\nSenior AI Engineer 2020-01 to Present\n"
                b"Scaled LangGraph multi-agent workflows over PostgreSQL and Qdrant to 65k rps "
                b"with 40% latency reduction.\n"
            ),
        ),
        (
            f"liam_ortega_{run_id}.pdf",
            (
                b"%PDF-1.7\nName: Liam Ortega\nEmail: liam.ortega@acme-demo.dev\nSUMMARY\n"
                b"Full-Stack TypeScript Next.js Python FastAPI engineer.\n"
                b"EXPERIENCE\nFull-Stack Engineer 2021-04 to Present\n"
                b"Built Next.js real-time SSE dashboards with Python asyncio & Vector Search.\n"
            ),
        ),
    )

    batch_init = await bundle.container.initiate_screening_batch.execute(
        InitiateScreeningBatchRequestDTO(
            tenant_id=tid,
            requisition_id=req_uuid,
            batch_label=f"live-sim-{run_id}",
            files=tuple(
                BatchFileUploadSpecDTO(
                    filename=fname,
                    sha256_hex=SHA256Hash.from_bytes(payload).value,
                    file_size_bytes=len(payload),
                    mime_type="application/pdf",
                )
                for fname, payload in sim_cvs
            ),
            idempotency_key=f"demo-sim-batch-{run_id}",
            correlation_id=f"corr-sim-{run_id}",
        )
    )

    for (_fname, payload), instr in zip(sim_cvs, batch_init.upload_instructions, strict=True):
        await bundle.adapters.object_storage.put_bytes(
            tenant_id=tid,
            object_key=instr.object_key,
            payload=payload,
            content_type="application/pdf",
        )

    async def _run_simulated_workers() -> None:
        for idx, (_fname, _payload) in enumerate(sim_cvs):
            await asyncio.sleep(0.15 * (idx + 1))
            instr = batch_init.upload_instructions[idx]
            await bundle.container.process_document_task.execute(
                ProcessDocumentIngestionTaskRequestDTO(
                    tenant_id=tid,
                    batch_id=batch_init.batch_id,
                    task_id=instr.task_id,
                    object_key=instr.object_key,
                    correlation_id=f"corr-sim-task-{run_id}-{idx}",
                )
            )
        await bundle.outbox_relay.poll_and_dispatch(tenant_id=tid)

    task = asyncio.create_task(_run_simulated_workers())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return {
        "batch_id": str(batch_init.batch_id),
        "tenant_id": str(tid.value),
        "requisition_id": str(req_uuid),
        "file_count": len(sim_cvs),
        "sse_events_path": f"/v1/screening-batches/{batch_init.batch_id}/events",
        "status_path": f"/v1/screening-batches/{batch_init.batch_id}",
        "status": "STARTED",
    }


__all__ = [
    "DemoSeedManifest",
    "seed_interactive_demo_environment",
    "simulate_live_demo_batch",
]
