"""Stage 3/4 LangChain Structured Extraction Anti-Corruption Layer (ACL)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable

from talent_screener.application.common.exceptions import (
    ExternalAdapterFailureError,
    UnauthorizedTenantAccessError,
)
from talent_screener.application.common.ports.outbound import (
    ObjectStoragePort,
    ObservabilityTracerPort,
    PiiVaultPort,
)
from talent_screener.application.document_intelligence.dtos import (
    DocumentExtractionCommandDTO,
    ExtractedCandidateDossierDTO,
    ExtractedProjectDTO,
    ExtractedWorkExperienceDTO,
)
from talent_screener.domain.contexts.document_intelligence.value_objects import (
    ExtractedSkillClaim,
    ImpactMetric,
    SeniorityEstimate,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import (
    ConfidenceScore,
    SHA256Hash,
    TenantId,
)
from talent_screener.infrastructure.ai_adapters.langchain_parser.grounding_verifier import (
    GroundingVerifier,
    RejectedClaimRecord,
    compute_union_experience_months,
)
from talent_screener.infrastructure.ai_adapters.langchain_parser.normalizer import (
    DocumentSectionType,
    DocumentTextNormalizer,
    NormalizedDocumentTree,
)

# Canonical skill ontology mapping raw resume aliases to standardized taxonomy IDs.
_SKILL_TAXONOMY: tuple[tuple[str, str, str], ...] = (
    ("python", "SKILL_LANG_PYTHON", "Python"),
    ("asyncio", "SKILL_LANG_PYTHON", "Python Asyncio"),
    ("distributed systems", "SKILL_DISTRIBUTED_SYSTEMS", "Distributed Systems"),
    ("microservices", "SKILL_DISTRIBUTED_SYSTEMS", "Microservices"),
    ("kubernetes", "SKILL_KUBERNETES", "Kubernetes"),
    ("k8s", "SKILL_KUBERNETES", "Kubernetes"),
    ("postgresql", "SKILL_POSTGRESQL", "PostgreSQL"),
    ("postgres", "SKILL_POSTGRESQL", "PostgreSQL"),
    ("fastapi", "SKILL_FASTAPI", "FastAPI"),
    ("langchain", "SKILL_LANGCHAIN", "LangChain"),
    ("langgraph", "SKILL_LANGGRAPH", "LangGraph"),
    ("qdrant", "SKILL_VECTOR_SEARCH", "Qdrant"),
    ("pgvector", "SKILL_VECTOR_SEARCH", "pgvector"),
    ("vector search", "SKILL_VECTOR_SEARCH", "Vector Search"),
    ("rag", "SKILL_RAG", "RAG"),
    ("docker", "SKILL_DOCKER", "Docker"),
    ("redis", "SKILL_REDIS", "Redis"),
    ("kafka", "SKILL_KAFKA", "Apache Kafka"),
    ("typescript", "SKILL_TYPESCRIPT", "TypeScript"),
    ("next.js", "SKILL_REACT_NEXTJS", "Next.js"),
    ("react", "SKILL_REACT_NEXTJS", "React"),
    ("golang", "SKILL_LANG_GO", "Go"),
    ("rust", "SKILL_LANG_RUST", "Rust"),
)

_ACTION_ARCHITECT_RE = re.compile(
    r"\b(?:architected|designed|built|led|engineered|deployed|scaled|implemented|optimized)\b",
    re.I,
)
_DATE_RANGE_RE = re.compile(
    r"(?P<sy>(?:19|20)\d{2})(?:-(?P<sm>0[1-9]|1[0-2]))?\s*(?:to|[-\u2013\u2014])\s*"
    r"(?:(?P<curr>present|current|now)|(?P<ey>(?:19|20)\d{2})(?:-(?P<em>0[1-9]|1[0-2]))?)",
    re.I,
)
_PERCENT_METRIC_RE = re.compile(
    r"([^.\n;]*?\b(\d+(?:\.\d+)?)\s*%[^.\n;]*)",
    re.I,
)
_SCALE_RPS_METRIC_RE = re.compile(
    r"([^.\n;]*?\b(\d+(?:\.\d+)?)\s*k?\s*(?:req(?:uests)?/s(?:ec)?|rps|qps)[^.\n;]*)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class RawImpactMetricPayload:
    """Raw structured LLM output for a quantified engineering metric."""

    metric_type: str
    numeric_value: float
    unit: str
    source_quote: str


@dataclass(frozen=True, slots=True)
class RawSkillClaimPayload:
    """Raw structured LLM output for a skill claim."""

    canonical_skill_id: str
    raw_label: str
    source_quote: str
    claimed_depth: int = 3
    applied_months: int = 12
    recency_months: int = 0
    confidence: float = 0.9


@dataclass(frozen=True, slots=True)
class RawWorkExperiencePayload:
    """Raw structured LLM output for chronological work experience."""

    role_title: str
    normalized_role: str
    company_token: str
    start_year: int
    start_month: int
    end_year: int | None = None
    end_month: int | None = None
    is_current: bool = False
    employment_type: str = "FULL_TIME"


@dataclass(frozen=True, slots=True)
class RawProjectPayload:
    """Raw structured LLM output for a technical project artifact."""

    project_name: str
    scale_tier: str
    summary: str
    source_quote: str
    impact_metrics: tuple[RawImpactMetricPayload, ...] = ()


@dataclass(frozen=True, slots=True)
class RawStructuredDossierPayload:
    """Raw structured LLM payload produced inside the Infrastructure ACL boundary."""

    work_experiences: tuple[RawWorkExperiencePayload, ...] = ()
    projects: tuple[RawProjectPayload, ...] = ()
    skill_claims: tuple[RawSkillClaimPayload, ...] = ()
    seniority_level: str | None = None
    extraction_confidence: float = 0.9


@runtime_checkable
class StructuredExtractionRunnableProtocol(Protocol):
    """Protocol matching LangChain Runnable.ainvoke(...) structured extraction interface."""

    async def ainvoke(
        self, prompt_input: Mapping[str, object]
    ) -> RawStructuredDossierPayload | Mapping[str, object]: ...


StructuredLlmExtractorClientProtocol = StructuredExtractionRunnableProtocol


def canonicalize_skill_id(raw_label: str) -> str:
    """Map raw skill label aliases onto canonical domain skill ontology IDs."""
    lowered = raw_label.strip().lower()
    if lowered.startswith("skill_"):
        return raw_label.strip().upper()
    for alias, skill_id, _ in _SKILL_TAXONOMY:
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered):
            return skill_id
    slug = re.sub(r"[^A-Z0-9]+", "_", lowered.upper()).strip("_")
    return f"SKILL_{slug[:100]}" if slug else "SKILL_GENERAL"


def _infer_seniority_level(total_months: int, work_roles: tuple[str, ...]) -> str:
    titles = " ".join(work_roles).lower()
    if "principal" in titles:
        return "PRINCIPAL"
    if "staff" in titles or total_months >= 96:
        return "STAFF"
    if "director" in titles or "engineering manager" in titles:
        return "MANAGER_DIRECTOR"
    if "senior" in titles or total_months >= 48:
        return "SENIOR"
    if total_months >= 24:
        return "MID"
    if total_months > 0:
        return "JUNIOR"
    return "JUNIOR"


class DeterministicRuleBasedExtractorClient:
    """Offline deterministic structured extractor fulfilling LangChain Runnable protocol."""

    def __init__(self, *, reference_date: date | None = None) -> None:
        self._reference_date = reference_date

    async def ainvoke(
        self, prompt_input: Mapping[str, object]
    ) -> RawStructuredDossierPayload:
        """Deterministically extract experiences, projects, and skills from redacted text."""
        raw_text = str(prompt_input.get("redacted_text", ""))
        section_map_obj = prompt_input.get("sections", ())
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        # Extract chronological work experience intervals cleanly from EXPERIENCE/header lines.
        work_experiences: list[RawWorkExperiencePayload] = []
        ref = self._reference_date if self._reference_date is not None else utc_now().date()
        ref_idx = ref.year * 12 + (ref.month - 1)

        for idx, line in enumerate(lines):
            date_match = _DATE_RANGE_RE.search(line)
            if not date_match:
                continue
            sy = int(date_match.group("sy"))
            sm = int(date_match.group("sm") or 1)
            is_curr = date_match.group("curr") is not None
            ey = int(date_match.group("ey")) if date_match.group("ey") else None
            em = (
                int(date_match.group("em") or 12)
                if ey is not None
                else None
            )
            title_part = line[: date_match.start()].strip(" |-:,")
            if not title_part and idx > 0:
                title_part = lines[idx - 1][:100]
            role_title = (title_part or "Software Engineer")[:120]
            work_experiences.append(
                RawWorkExperiencePayload(
                    role_title=role_title,
                    normalized_role=(
                        "AI_ML_ENGINEER"
                        if any(k in role_title.lower() for k in ("ai", "ml", "llm"))
                        else "BACKEND_ENGINEER"
                    ),
                    company_token=f"ORG_{len(work_experiences) + 1}",
                    start_year=sy,
                    start_month=sm,
                    end_year=None if is_curr else ey,
                    end_month=None if is_curr else em,
                    is_current=is_curr,
                    employment_type="FULL_TIME",
                )
            )

        # Estimate default skill tenure & recency per role interval or section membership.
        role_months_max = 12
        role_recency_min = 0
        if work_experiences:
            durations: list[int] = []
            recencies: list[int] = []
            for we in work_experiences:
                s_idx = we.start_year * 12 + (we.start_month - 1)
                if we.is_current:
                    durations.append(max(1, ref_idx - s_idx + 1))
                    recencies.append(0)
                elif we.end_year is not None and we.end_month is not None:
                    e_idx = we.end_year * 12 + (we.end_month - 1)
                    durations.append(max(1, e_idx - s_idx + 1))
                    recencies.append(max(0, ref_idx - e_idx))
            if durations:
                role_months_max = sum(durations)
            if recencies:
                role_recency_min = min(recencies)

        # Extract grounded skill claims matching lines verbatim.
        sections_by_line: dict[str, str] = {}
        if isinstance(section_map_obj, (list, tuple)):
            for item in section_map_obj:
                if isinstance(item, Mapping):
                    sec_type = str(item.get("section_type", "OTHER"))
                    sec_content = str(item.get("content", ""))
                    for s_line in sec_content.splitlines():
                        if s_line.strip():
                            sections_by_line[s_line.strip()] = sec_type

        skills_by_id: dict[str, RawSkillClaimPayload] = {}
        for line in lines:
            sec_type = sections_by_line.get(line, "OTHER")
            has_action = bool(_ACTION_ARCHITECT_RE.search(line))
            line_recency = role_recency_min
            dm = _DATE_RANGE_RE.search(line)
            if dm and dm.group("ey"):
                e_idx = int(dm.group("ey")) * 12 + (int(dm.group("em") or 12) - 1)
                line_recency = max(0, ref_idx - e_idx)

            for alias, canonical_id, display_label in _SKILL_TAXONOMY:
                if not re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", line, re.I):
                    continue
                if sec_type == DocumentSectionType.SKILLS and not has_action:
                    depth = 1
                elif (
                    sec_type in {DocumentSectionType.EXPERIENCE, DocumentSectionType.PROJECTS}
                    or has_action
                ):
                    has_high_impact = (
                        "%" in line
                        or "architected" in line.lower()
                        or "k " in line.lower()
                    )
                    depth = 4 if has_high_impact else 3
                else:
                    depth = 2
                applied = (
                    max(6, role_months_max)
                    if depth >= 3
                    else (12 if work_experiences else 6)
                )
                quote = line[:480]
                prev = skills_by_id.get(canonical_id)
                if prev is None or depth > prev.claimed_depth:
                    skills_by_id[canonical_id] = RawSkillClaimPayload(
                        canonical_skill_id=canonical_id,
                        raw_label=display_label,
                        source_quote=quote,
                        claimed_depth=depth,
                        applied_months=min(720, applied),
                        recency_months=min(720, line_recency),
                        confidence=0.95 if depth >= 3 else 0.85,
                    )

        # Extract project / achievement artifacts from PROJECTS or action bullet lines.
        projects: list[RawProjectPayload] = []
        for line in lines:
            sec_type = sections_by_line.get(line, "OTHER")
            if sec_type != DocumentSectionType.PROJECTS and not _ACTION_ARCHITECT_RE.search(line):
                continue
            metrics: list[RawImpactMetricPayload] = []
            pm = _PERCENT_METRIC_RE.search(line)
            if pm:
                metrics.append(
                    RawImpactMetricPayload(
                        metric_type="PERCENTAGE_IMPROVEMENT",
                        numeric_value=float(pm.group(2)),
                        unit="PERCENT",
                        source_quote=pm.group(1).strip()[:400],
                    )
                )
            rm = _SCALE_RPS_METRIC_RE.search(line)
            if rm:
                raw_num = float(rm.group(2))
                rps_val = raw_num * 1000.0 if "k" in rm.group(0).lower() else raw_num
                metrics.append(
                    RawImpactMetricPayload(
                        metric_type="THROUGHPUT_RPS",
                        numeric_value=rps_val,
                        unit="RPS",
                        source_quote=rm.group(1).strip()[:400],
                    )
                )
            clean_bullet = line.lstrip("-* ").strip()
            projects.append(
                RawProjectPayload(
                    project_name=clean_bullet[:80],
                    scale_tier=(
                        "HIGH_THROUGHPUT_PRODUCTION" if metrics else "DEPARTMENTAL_INTERNAL"
                    ),
                    summary=clean_bullet[:580],
                    source_quote=clean_bullet[:480],
                    impact_metrics=tuple(metrics),
                )
            )
            if len(projects) >= 5:
                break

        return RawStructuredDossierPayload(
            work_experiences=tuple(work_experiences),
            projects=tuple(projects),
            skill_claims=tuple(skills_by_id.values()),
            extraction_confidence=0.94 if skills_by_id else 0.65,
        )


def _coerce_raw_payload(
    raw: RawStructuredDossierPayload | Mapping[str, object] | Any,
) -> RawStructuredDossierPayload:
    if isinstance(raw, RawStructuredDossierPayload):
        return raw
    if hasattr(raw, "model_dump") and callable(raw.model_dump):
        data = raw.model_dump()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        raise ExternalAdapterFailureError(
            f"Unsupported LLM extraction payload type: {type(raw).__name__}.",
            code="EXTRACTION_SCHEMA_ERROR",
            retryable=False,
        )

    try:
        raw_work = data.get("work_experiences", ())
        works = tuple(
            w
            if isinstance(w, RawWorkExperiencePayload)
            else RawWorkExperiencePayload(
                role_title=str(w["role_title"]),
                normalized_role=str(w.get("normalized_role", "SOFTWARE_ENGINEER")),
                company_token=str(w.get("company_token", "ORG_REDACTED")),
                start_year=int(w["start_year"]),
                start_month=int(w["start_month"]),
                end_year=int(w["end_year"]) if w.get("end_year") is not None else None,
                end_month=int(w["end_month"]) if w.get("end_month") is not None else None,
                is_current=bool(w.get("is_current", False)),
                employment_type=str(w.get("employment_type", "FULL_TIME")),
            )
            for w in raw_work
        )
        raw_projs = data.get("projects", ())
        projs: list[RawProjectPayload] = []
        for p in raw_projs:
            if isinstance(p, RawProjectPayload):
                projs.append(p)
            else:
                metrics = tuple(
                    m
                    if isinstance(m, RawImpactMetricPayload)
                    else RawImpactMetricPayload(
                        metric_type=str(m["metric_type"]),
                        numeric_value=float(m["numeric_value"]),
                        unit=str(m["unit"]),
                        source_quote=str(m["source_quote"]),
                    )
                    for m in p.get("impact_metrics", ())
                )
                projs.append(
                    RawProjectPayload(
                        project_name=str(p["project_name"]),
                        scale_tier=str(p.get("scale_tier", "UNSPECIFIED")),
                        summary=str(p["summary"]),
                        source_quote=str(p["source_quote"]),
                        impact_metrics=metrics,
                    )
                )
        raw_skills = data.get("skill_claims", ())
        skills = tuple(
            s
            if isinstance(s, RawSkillClaimPayload)
            else RawSkillClaimPayload(
                canonical_skill_id=canonicalize_skill_id(str(s["canonical_skill_id"])),
                raw_label=str(s["raw_label"]),
                source_quote=str(s["source_quote"]),
                claimed_depth=int(s.get("claimed_depth", 3)),
                applied_months=int(s.get("applied_months", 12)),
                recency_months=int(s.get("recency_months", 0)),
                confidence=float(s.get("confidence", 0.9)),
            )
            for s in raw_skills
        )
        return RawStructuredDossierPayload(
            work_experiences=works,
            projects=tuple(projs),
            skill_claims=skills,
            seniority_level=(
                str(data["seniority_level"]) if data.get("seniority_level") is not None else None
            ),
            extraction_confidence=float(data.get("extraction_confidence", 0.9)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExternalAdapterFailureError(
            f"Malformed structured extraction schema payload: {exc}",
            code="EXTRACTION_SCHEMA_ERROR",
            retryable=False,
        ) from exc


@dataclass(frozen=True, slots=True)
class ParserTelemetrySnapshot:
    """Audit telemetry recorded per extraction invocation (zero PII)."""

    stripped_invisible_chars: int
    rejected_hallucinations: tuple[RejectedClaimRecord, ...] = field(default_factory=tuple)


class LangChainDocumentParserAdapter:
    """Anti-Corruption Layer adapter implementing DocumentParserPort (Stages 2-4)."""

    def __init__(
        self,
        *,
        object_storage: ObjectStoragePort,
        pii_vault: PiiVaultPort,
        llm_client: StructuredExtractionRunnableProtocol | None = None,
        normalizer: DocumentTextNormalizer | None = None,
        grounding_verifier: GroundingVerifier | None = None,
        tracer: ObservabilityTracerPort | None = None,
        grounding_threshold: float = 0.85,
    ) -> None:
        self._object_storage = object_storage
        self._pii_vault = pii_vault
        self._llm_client: StructuredExtractionRunnableProtocol = (
            llm_client if llm_client is not None else DeterministicRuleBasedExtractorClient()
        )
        self._normalizer = normalizer if normalizer is not None else DocumentTextNormalizer()
        self._verifier = (
            grounding_verifier
            if grounding_verifier is not None
            else GroundingVerifier(grounding_threshold=grounding_threshold)
        )
        self._tracer = tracer
        self.last_telemetry: ParserTelemetrySnapshot | None = None

    async def parse_and_extract(
        self, command: DocumentExtractionCommandDTO
    ) -> ExtractedCandidateDossierDTO:
        """Fetch raw CV, normalize text, redact PII, invoke LLM ACL, and verify grounding."""
        if not isinstance(command.tenant_id, TenantId):
            raise UnauthorizedTenantAccessError(
                "Valid TenantId is required for document parsing.",
                code="MISSING_TENANT_CONTEXT",
            )

        raw_bytes = await self._object_storage.get_bytes(
            tenant_id=command.tenant_id,
            object_key=command.document_stream_uri,
        )
        actual_sha = SHA256Hash.from_bytes(raw_bytes)
        if actual_sha != command.expected_sha256:
            raise ExternalAdapterFailureError(
                "Object SHA-256 mismatch detected prior to document normalization.",
                code="CHECKSUM_MISMATCH",
                retryable=False,
            )

        normalized_tree: NormalizedDocumentTree = self._normalizer.normalize_bytes(
            payload=raw_bytes,
            mime_type=command.mime_type,
        )
        candidate_token = generate_uuid7()

        # Zero-Trust PII boundary: redact & vault all PII before LLM prompt construction.
        vault_outcome = await self._pii_vault.tokenize_and_vault(
            tenant_id=command.tenant_id,
            candidate_profile_id=candidate_token,
            raw_text=normalized_tree.full_text,
        )

        # Re-segment anonymized text so line/section coordinates stay consistent after redaction.
        redacted_tree = self._normalizer.normalize_text(
            raw_text=vault_outcome.anonymized_text,
            mime_type=command.mime_type,
            source_sha256=normalized_tree.source_sha256,
        )

        prompt_payload: dict[str, object] = {
            "taxonomy_version": command.skill_taxonomy_version,
            "redacted_text": redacted_tree.full_text,
            "delimited_prompt": (
                "<untrusted_cv_content>\n"
                f"{redacted_tree.full_text}\n"
                "</untrusted_cv_content>"
            ),
            "sections": [
                {
                    "section_type": sec.section_type.value,
                    "heading": sec.heading,
                    "content": sec.content,
                }
                for sec in redacted_tree.sections
            ],
        }

        try:
            raw_llm_output = await self._llm_client.ainvoke(prompt_payload)
        except TimeoutError as exc:
            raise ExternalAdapterFailureError(
                f"Structured extraction timed out: {exc}",
                code="UPSTREAM_LLM_TIMEOUT",
                retryable=True,
            ) from exc
        except ConnectionError as exc:
            raise ExternalAdapterFailureError(
                f"Structured extraction connection failed: {exc}",
                code="UPSTREAM_LLM_CONNECTION_ERROR",
                retryable=True,
            ) from exc

        structured = _coerce_raw_payload(raw_llm_output)

        # Validate chronological work history date ranges and compute verified duration.
        verified_experiences: list[ExtractedWorkExperienceDTO] = []
        verified_date_ranges = []
        rejected_claims: list[RejectedClaimRecord] = []
        for we in structured.work_experiences:
            checked = self._verifier.validate_chronological_range(
                start_year=we.start_year,
                start_month=we.start_month,
                end_year=we.end_year,
                end_month=we.end_month,
                is_current=we.is_current,
            )
            if checked is None:
                rejected_claims.append(
                    RejectedClaimRecord(
                        category="WORK_EXPERIENCE",
                        identifier=we.role_title,
                        raw_quote=f"{we.start_year}-{we.start_month}",
                        similarity_score=0.0,
                        reason="Invalid or reversed chronological date interval.",
                    )
                )
                continue
            verified_date_ranges.append(checked)
            verified_experiences.append(
                ExtractedWorkExperienceDTO(
                    role_title=we.role_title.strip()[:120],
                    normalized_role=we.normalized_role.strip()[:80],
                    company_token=we.company_token.strip()[:120],
                    start_year=checked.start_year,
                    start_month=checked.start_month,
                    end_year=checked.end_year,
                    end_month=checked.end_month,
                    is_current=checked.is_current,
                    employment_type=we.employment_type.strip().upper()[:40],
                )
            )

        # Anti-hallucination verification for every extracted skill claim.
        candidate_claims = [
            ExtractedSkillClaim(
                canonical_skill_id=canonicalize_skill_id(sc.canonical_skill_id),
                raw_label=sc.raw_label.strip()[:120],
                source_quote=sc.source_quote.strip()[:480] or "N/A",
                claimed_depth=max(1, min(5, sc.claimed_depth)),
                applied_months=max(0, min(720, sc.applied_months)),
                recency_months=max(0, min(720, sc.recency_months)),
                confidence=ConfidenceScore(max(0.0, min(1.0, sc.confidence))),
            )
            for sc in structured.skill_claims
            if sc.source_quote and len(sc.source_quote.strip()) >= 3
        ]
        for sc in structured.skill_claims:
            if not sc.source_quote or len(sc.source_quote.strip()) < 3:
                rejected_claims.append(
                    RejectedClaimRecord(
                        category="SKILL",
                        identifier=sc.canonical_skill_id,
                        raw_quote=sc.source_quote or "",
                        similarity_score=0.0,
                        reason="Empty or missing verbatim source_quote.",
                    )
                )

        verified_skills, rejected_skill_claims = self._verifier.verify_skill_claims(
            claims=candidate_claims,
            normalized_source_text=redacted_tree.full_text,
        )
        rejected_claims.extend(rejected_skill_claims)

        if not verified_skills:
            self.last_telemetry = ParserTelemetrySnapshot(
                stripped_invisible_chars=normalized_tree.stripped_invisible_chars,
                rejected_hallucinations=tuple(rejected_claims),
            )
            raise ExternalAdapterFailureError(
                "No extracted skill claims passed anti-hallucination grounding verification.",
                code="NO_GROUNDED_SKILLS_EXTRACTED",
                retryable=False,
            )

        # Verify project quotes and impact metrics cleanly.
        verified_projects: list[ExtractedProjectDTO] = []
        for proj in structured.projects:
            proj_outcome = self._verifier.verify_quote(
                source_quote=proj.source_quote,
                normalized_source_text=redacted_tree.full_text,
            )
            if not proj_outcome.accepted:
                rejected_claims.append(
                    RejectedClaimRecord(
                        category="PROJECT",
                        identifier=proj.project_name,
                        raw_quote=proj.source_quote,
                        similarity_score=proj_outcome.similarity_score,
                        reason="Project quote failed grounding verification.",
                    )
                )
                continue
            metric_candidates = [
                ImpactMetric(
                    metric_type=m.metric_type,
                    numeric_value=m.numeric_value,
                    unit=m.unit,
                    source_quote=m.source_quote,
                )
                for m in proj.impact_metrics
                if m.source_quote and len(m.source_quote.strip()) >= 3
            ]
            verified_metrics, rejected_metrics = self._verifier.verify_impact_metrics(
                metrics=metric_candidates,
                normalized_source_text=redacted_tree.full_text,
            )
            rejected_claims.extend(rejected_metrics)
            resolved_proj_quote = (
                proj.source_quote
                if proj_outcome.similarity_score == 1.0
                else proj_outcome.canonical_quote
            )
            verified_projects.append(
                ExtractedProjectDTO(
                    project_name=proj.project_name.strip()[:120],
                    scale_tier=proj.scale_tier.strip().upper(),
                    summary=proj.summary.strip()[:580],
                    source_quote=resolved_proj_quote,
                    impact_metrics=verified_metrics,
                )
            )

        verified_months = compute_union_experience_months(verified_date_ranges)
        seniority_level = _infer_seniority_level(
            verified_months,
            tuple(w.role_title for w in verified_experiences),
        )
        extraction_conf = max(
            0.0,
            min(
                1.0,
                structured.extraction_confidence
                - (0.05 * min(5, len(rejected_claims))),
            ),
        )

        self.last_telemetry = ParserTelemetrySnapshot(
            stripped_invisible_chars=normalized_tree.stripped_invisible_chars,
            rejected_hallucinations=tuple(rejected_claims),
        )
        if self._tracer is not None:
            self._tracer.record_metric(
                name="document_parser.rejected_hallucinations",
                value=float(len(rejected_claims)),
                tags={"tenant_id": str(command.tenant_id.value)},
            )
            self._tracer.record_metric(
                name="document_parser.stripped_invisible_chars",
                value=float(normalized_tree.stripped_invisible_chars),
                tags={"tenant_id": str(command.tenant_id.value)},
            )

        return ExtractedCandidateDossierDTO(
            anonymized_candidate_token=candidate_token,
            schema_version="1.0.0",
            raw_redacted_text=redacted_tree.full_text,
            pii_redaction_map=vault_outcome.pii_redaction_map,
            work_experiences=tuple(verified_experiences),
            projects=tuple(verified_projects),
            skill_claims=verified_skills,
            seniority_estimate=SeniorityEstimate(
                level=seniority_level,
                verified_experience_months=verified_months,
                confidence=ConfidenceScore(extraction_conf),
            ),
            extraction_confidence=ConfidenceScore(extraction_conf),
        )
