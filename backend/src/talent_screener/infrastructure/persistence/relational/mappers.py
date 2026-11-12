"""Bidirectional Data Mappers converting SQLite/PostgreSQL rows to Domain Aggregates."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from talent_screener.application.document_intelligence.dtos import CandidateProfileReadDTO
from talent_screener.domain.contexts.document_intelligence import (
    BatchStatus,
    CandidateProfile,
    DocumentIngestionTask,
    ExtractedSkillClaim,
    FileContentHash,
    ImpactMetric,
    PiiRedactionMap,
    ProjectComplexityRecord,
    ScreeningBatch,
    SeniorityEstimate,
    TaskIngestionStatus,
    WorkExperienceEntry,
)
from talent_screener.domain.contexts.document_intelligence.matching import (
    CandidateTier,
    CompetencyAlignmentItem,
    EpistemicSkillGap,
    GapType,
    HybridMatchVector,
    RecencyDecayFactor,
    SemanticMatchResult,
)
from talent_screener.domain.contexts.evaluation_analytics import (
    AuthenticityIndex,
    CompetencyScorecardItem,
    CompositeRubricScore,
    ConfidenceInterval,
    EvaluationReport,
    HiringRecommendationBand,
    IntegrityAuditEntry,
    RecruiterOverrideAudit,
)
from talent_screener.domain.contexts.identity_governance import (
    ConsentRecord,
    ConsentScope,
    ConsentStatus,
    QuotaPolicy,
    RetentionPolicy,
    TenantAccount,
)
from talent_screener.domain.contexts.live_interview import (
    AdaptiveTurn,
    DifficultyLevel,
    IntegrityFlag,
    InterviewSession,
    PromptHash,
    ResponseLatencyMetric,
    SessionStatus,
    SocraticProbe,
    TelemetrySnapshot,
    TurnEvaluationScore,
)
from talent_screener.domain.contexts.recruitment import (
    CompetencyCriterion,
    CompetencyWeightVector,
    GatekeeperRule,
    HardSkillGatekeeperSet,
    JobRequisition,
    RequisitionStatus,
    SeniorityLevel,
    TierThresholdConfig,
)
from talent_screener.domain.shared.base import AggregateRoot
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import (
    ConfidenceScore,
    SHA256Hash,
    TenantId,
    VersionNumber,
)


def _json_dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _parse_ts(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    return datetime.fromisoformat(raw)


def _init_aggregate_slots[T: AggregateRoot[Any]](
    cls: type[T],
    entity_id: Any,
    tenant_id: TenantId,
    version: VersionNumber,
) -> T:
    """Instantiate an AggregateRoot bypassing event-emitting __init__ during SQL hydration."""
    instance: T = object.__new__(cls)
    instance._id = entity_id
    instance._tenant_id = tenant_id
    instance._version = version
    instance._domain_events = []
    return instance


def _serialize_integrity_flag(flag: IntegrityFlag) -> dict[str, object]:
    return {
        "flag_code": flag.flag_code,
        "severity": flag.severity,
        "turn_number": flag.turn_number,
        "confidence": float(flag.confidence),
        "evidence_summary": flag.evidence_summary,
    }


def _deserialize_integrity_flag(data: Mapping[str, Any]) -> IntegrityFlag:
    return IntegrityFlag(
        flag_code=str(data["flag_code"]),
        severity=str(data["severity"]),
        turn_number=int(data["turn_number"]),
        confidence=ConfidenceScore(float(data["confidence"])),
        evidence_summary=str(data["evidence_summary"]),
    )


def _serialize_hybrid_vector(vec: HybridMatchVector) -> dict[str, object]:
    return {
        "dense_similarity": vec.dense_similarity,
        "sparse_bm25_score": vec.sparse_bm25_score,
        "experience_alignment": vec.experience_alignment,
        "alpha_dense": vec.alpha_dense,
        "beta_sparse": vec.beta_sparse,
        "gamma_recency": vec.gamma_recency,
        "gatekeeper_multiplier": vec.gatekeeper_multiplier,
        "lambda_rate": vec.recency_decay.lambda_rate,
        "recency_months_elapsed": vec.recency_months_elapsed,
    }


def _deserialize_hybrid_vector(data: Mapping[str, Any]) -> HybridMatchVector:
    return HybridMatchVector(
        dense_similarity=float(data["dense_similarity"]),
        sparse_bm25_score=float(data["sparse_bm25_score"]),
        experience_alignment=float(data["experience_alignment"]),
        alpha_dense=float(data["alpha_dense"]),
        beta_sparse=float(data["beta_sparse"]),
        gamma_recency=float(data["gamma_recency"]),
        gatekeeper_multiplier=float(data["gatekeeper_multiplier"]),
        recency_decay=RecencyDecayFactor(lambda_rate=float(data["lambda_rate"])),
        recency_months_elapsed=int(data["recency_months_elapsed"]),
    )


# ---------------------------------------------------------------------------
# TenantAccount
# ---------------------------------------------------------------------------


def tenant_account_to_row(tenant: TenantAccount) -> dict[str, object]:
    """Serialize TenantAccount aggregate into relational column parameters."""
    qp = tenant.quota_policy
    rp = tenant.retention_policy
    return {
        "tenant_id": str(tenant.tenant_id.value),
        "organization_slug": tenant.organization_slug,
        "display_name": tenant.display_name,
        "quota_policy_json": _json_dumps(
            {
                "max_concurrent_batches": qp.max_concurrent_batches,
                "max_files_per_batch": qp.max_files_per_batch,
                "max_monthly_interviews": qp.max_monthly_interviews,
                "max_tokens_per_interview": qp.max_tokens_per_interview,
                "subscription_tier": qp.subscription_tier,
            }
        ),
        "retention_policy_json": _json_dumps(
            {
                "pii_retention_days": rp.pii_retention_days,
                "audit_log_retention_days": rp.audit_log_retention_days,
                "erasure_sla_hours": rp.erasure_sla_hours,
                "auto_shred_on_expiry": rp.auto_shred_on_expiry,
            }
        ),
        "is_active": int(tenant.is_active),
        "active_batches_count": tenant.active_batches_count,
        "monthly_interviews_count": tenant.monthly_interviews_count,
        "version": int(tenant.version),
        "updated_at": utc_now().isoformat(),
    }


def row_to_tenant_account(row: sqlite3.Row) -> TenantAccount:
    """Hydrate TenantAccount aggregate losslessly from a SQL row."""
    tenant_id = TenantId.from_str(str(row["tenant_id"]))
    qp_data = json.loads(str(row["quota_policy_json"]))
    rp_data = json.loads(str(row["retention_policy_json"]))
    agg = _init_aggregate_slots(
        TenantAccount,
        tenant_id,
        tenant_id,
        VersionNumber(int(row["version"])),
    )
    agg._organization_slug = str(row["organization_slug"])
    agg._display_name = str(row["display_name"])
    agg._quota_policy = QuotaPolicy(
        max_concurrent_batches=int(qp_data["max_concurrent_batches"]),
        max_files_per_batch=int(qp_data["max_files_per_batch"]),
        max_monthly_interviews=int(qp_data["max_monthly_interviews"]),
        max_tokens_per_interview=int(qp_data["max_tokens_per_interview"]),
        subscription_tier=str(qp_data["subscription_tier"]),
    )
    agg._retention_policy = RetentionPolicy(
        pii_retention_days=int(rp_data["pii_retention_days"]),
        audit_log_retention_days=int(rp_data["audit_log_retention_days"]),
        erasure_sla_hours=int(rp_data["erasure_sla_hours"]),
        auto_shred_on_expiry=bool(rp_data["auto_shred_on_expiry"]),
    )
    agg._is_active = bool(row["is_active"])
    agg._active_batches_count = int(row["active_batches_count"])
    agg._monthly_interviews_count = int(row["monthly_interviews_count"])
    return agg


# ---------------------------------------------------------------------------
# ConsentRecord
# ---------------------------------------------------------------------------


def consent_record_to_row(record: ConsentRecord) -> dict[str, object]:
    """Serialize ConsentRecord aggregate into relational column parameters."""
    rp = record.retention_policy
    return {
        "record_id": str(record.id),
        "tenant_id": str(record.tenant_id.value),
        "candidate_id": str(record.candidate_id),
        "granted_scopes_json": _json_dumps(sorted(scope.value for scope in record.granted_scopes)),
        "retention_policy_json": _json_dumps(
            {
                "pii_retention_days": rp.pii_retention_days,
                "audit_log_retention_days": rp.audit_log_retention_days,
                "erasure_sla_hours": rp.erasure_sla_hours,
                "auto_shred_on_expiry": rp.auto_shred_on_expiry,
            }
        ),
        "granted_at": record.granted_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "status": record.status.value,
        "erasure_requested_at": (
            record.erasure_requested_at.isoformat()
            if record.erasure_requested_at is not None
            else None
        ),
        "shredded_at": record.shredded_at.isoformat() if record.shredded_at is not None else None,
        "version": int(record.version),
    }


def row_to_consent_record(row: sqlite3.Row) -> ConsentRecord:
    """Hydrate ConsentRecord aggregate losslessly from a SQL row."""
    tenant_id = TenantId.from_str(str(row["tenant_id"]))
    rp_data = json.loads(str(row["retention_policy_json"]))
    agg = _init_aggregate_slots(
        ConsentRecord,
        UUID(str(row["record_id"])),
        tenant_id,
        VersionNumber(int(row["version"])),
    )
    agg._candidate_id = UUID(str(row["candidate_id"]))
    agg._granted_scopes = frozenset(
        ConsentScope(s) for s in json.loads(str(row["granted_scopes_json"]))
    )
    agg._retention_policy = RetentionPolicy(
        pii_retention_days=int(rp_data["pii_retention_days"]),
        audit_log_retention_days=int(rp_data["audit_log_retention_days"]),
        erasure_sla_hours=int(rp_data["erasure_sla_hours"]),
        auto_shred_on_expiry=bool(rp_data["auto_shred_on_expiry"]),
    )
    granted_at = _parse_ts(str(row["granted_at"]))
    expires_at = _parse_ts(str(row["expires_at"]))
    assert granted_at is not None and expires_at is not None
    agg._granted_at = granted_at
    agg._expires_at = expires_at
    agg._status = ConsentStatus(str(row["status"]))
    agg._erasure_requested_at = _parse_ts(
        str(row["erasure_requested_at"]) if row["erasure_requested_at"] is not None else None
    )
    agg._shredded_at = _parse_ts(
        str(row["shredded_at"]) if row["shredded_at"] is not None else None
    )
    return agg


# ---------------------------------------------------------------------------
# JobRequisition & CompetencyCriterion
# ---------------------------------------------------------------------------


def job_requisition_to_rows(
    req: JobRequisition,
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    """Serialize JobRequisition and child CompetencyCriterion entities into SQL parameters."""
    gatekeepers_payload = [
        {
            "canonical_skill_id": rule.canonical_skill_id,
            "min_applied_months": rule.min_applied_months,
            "strict_disqualification": rule.strict_disqualification,
        }
        for rule in sorted(req.gatekeepers.rules, key=lambda r: r.canonical_skill_id)
    ]
    thresholds = req.tier_thresholds
    parent_row: dict[str, object] = {
        "requisition_id": str(req.id),
        "tenant_id": str(req.tenant_id.value),
        "title": req.title,
        "description": req.description,
        "seniority_level": req.seniority_level.value,
        "status": req.status.value,
        "gatekeepers_json": _json_dumps(gatekeepers_payload),
        "tier_thresholds_json": _json_dumps(
            {
                "fast_track_threshold": thresholds.fast_track_threshold,
                "interview_threshold": thresholds.interview_threshold,
                "review_threshold": thresholds.review_threshold,
            }
        ),
        "weight_vector_json": _json_dumps(
            [[key, weight] for key, weight in req.weight_vector.weights]
        ),
        "interview_timebox_minutes": req.interview_timebox_minutes,
        "rubric_version": int(req.rubric_version),
        "rubric_version_id": str(req.rubric_version_id),
        "active_screening_batch_ids_json": _json_dumps(
            sorted(str(uid) for uid in req.active_screening_batch_ids)
        ),
        "active_interview_session_ids_json": _json_dumps(
            sorted(str(uid) for uid in req.active_interview_session_ids)
        ),
        "has_completed_interviews": int(req._has_completed_interviews),
        "version": int(req.version),
    }
    child_rows = tuple(
        {
            "criterion_id": str(item.id),
            "requisition_id": str(req.id),
            "tenant_id": str(req.tenant_id.value),
            "sort_order": idx,
            "name": item.name,
            "canonical_skill_id": item.canonical_skill_id,
            "weight": item.weight,
            "target_depth": item.target_depth,
            "is_gatekeeper": int(item.is_gatekeeper),
            "raw_importance": item.raw_importance,
            "ideal_anchor_descriptor": item.ideal_anchor_descriptor,
        }
        for idx, item in enumerate(req.criteria)
    )
    return parent_row, child_rows


def row_to_job_requisition(
    row: sqlite3.Row,
    criteria_rows: tuple[sqlite3.Row, ...],
) -> JobRequisition:
    """Hydrate JobRequisition aggregate and CompetencyCriterion children from SQL rows."""
    tenant_id = TenantId.from_str(str(row["tenant_id"]))
    criteria = [
        CompetencyCriterion(
            criterion_id=UUID(str(c_row["criterion_id"])),
            name=str(c_row["name"]),
            canonical_skill_id=str(c_row["canonical_skill_id"]),
            weight=float(c_row["weight"]),
            target_depth=int(c_row["target_depth"]),
            is_gatekeeper=bool(c_row["is_gatekeeper"]),
            raw_importance=float(c_row["raw_importance"]),
            ideal_anchor_descriptor=str(c_row["ideal_anchor_descriptor"]),
        )
        for c_row in criteria_rows
    ]
    gk_items = json.loads(str(row["gatekeepers_json"]))
    gatekeepers = HardSkillGatekeeperSet(
        rules=frozenset(
            GatekeeperRule(
                canonical_skill_id=str(g["canonical_skill_id"]),
                min_applied_months=int(g["min_applied_months"]),
                strict_disqualification=bool(g["strict_disqualification"]),
            )
            for g in gk_items
        )
    )
    th_data = json.loads(str(row["tier_thresholds_json"]))
    thresholds = TierThresholdConfig(
        fast_track_threshold=float(th_data["fast_track_threshold"]),
        interview_threshold=float(th_data["interview_threshold"]),
        review_threshold=float(th_data["review_threshold"]),
    )
    weights = CompetencyWeightVector(
        weights=tuple((str(k), float(v)) for k, v in json.loads(str(row["weight_vector_json"])))
    )

    agg = _init_aggregate_slots(
        JobRequisition,
        UUID(str(row["requisition_id"])),
        tenant_id,
        VersionNumber(int(row["version"])),
    )
    agg._title = str(row["title"])
    agg._description = str(row["description"])
    agg._seniority_level = SeniorityLevel(str(row["seniority_level"]))
    agg._status = RequisitionStatus(str(row["status"]))
    agg._criteria = criteria
    agg._weight_vector = weights
    agg._gatekeepers = gatekeepers
    agg._tier_thresholds = thresholds
    agg._interview_timebox_minutes = int(row["interview_timebox_minutes"])
    agg._rubric_version = VersionNumber(int(row["rubric_version"]))
    agg._rubric_version_id = UUID(str(row["rubric_version_id"]))
    agg._active_screening_batch_ids = {
        UUID(uid) for uid in json.loads(str(row["active_screening_batch_ids_json"]))
    }
    agg._active_interview_session_ids = {
        UUID(uid) for uid in json.loads(str(row["active_interview_session_ids_json"]))
    }
    agg._has_completed_interviews = bool(row["has_completed_interviews"])
    return agg


# ---------------------------------------------------------------------------
# ScreeningBatch & DocumentIngestionTask
# ---------------------------------------------------------------------------


def screening_batch_to_rows(
    batch: ScreeningBatch,
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    """Serialize ScreeningBatch and child DocumentIngestionTask rows."""
    parent_row: dict[str, object] = {
        "batch_id": str(batch.id),
        "tenant_id": str(batch.tenant_id.value),
        "requisition_id": str(batch.requisition_id),
        "batch_label": batch.batch_label,
        "total_files": batch.total_files,
        "status": batch.status.value,
        "completed_count": batch.completed_count,
        "failed_count": batch.failed_count,
        "pending_count": batch.pending_count,
        "seen_hashes_json": _json_dumps(sorted(batch._seen_hashes)),
        "version": int(batch.version),
    }
    task_rows: tuple[dict[str, object], ...] = tuple(
        {
            "task_id": str(task.id),
            "batch_id": str(batch.id),
            "tenant_id": str(batch.tenant_id.value),
            "sort_order": idx,
            "filename": task.filename,
            "sha256_hex": task.file_hash.sha256.value,
            "file_size_bytes": task.file_hash.file_size_bytes,
            "mime_type": task.file_hash.mime_type,
            "status": task.status.value,
            "candidate_profile_id": (
                str(task.candidate_profile_id)
                if task.candidate_profile_id is not None
                else None
            ),
            "error_code": task.error_code,
            "error_message": task.error_message,
            "retry_count": task.retry_count,
        }
        for idx, task in enumerate(batch.tasks)
    )
    return parent_row, task_rows


def row_to_screening_batch(
    row: sqlite3.Row,
    task_rows: tuple[sqlite3.Row, ...],
) -> ScreeningBatch:
    """Hydrate ScreeningBatch aggregate and child DocumentIngestionTask entities."""
    tenant_id = TenantId.from_str(str(row["tenant_id"]))
    tasks: dict[UUID, DocumentIngestionTask] = {}
    for t_row in task_rows:
        file_hash = FileContentHash(
            sha256=SHA256Hash(str(t_row["sha256_hex"])),
            file_size_bytes=int(t_row["file_size_bytes"]),
            mime_type=str(t_row["mime_type"]),
        )
        task = DocumentIngestionTask(
            task_id=UUID(str(t_row["task_id"])),
            filename=str(t_row["filename"]),
            file_hash=file_hash,
            status=TaskIngestionStatus(str(t_row["status"])),
        )
        task._candidate_profile_id = (
            UUID(str(t_row["candidate_profile_id"]))
            if t_row["candidate_profile_id"] is not None
            else None
        )
        task._error_code = str(t_row["error_code"]) if t_row["error_code"] is not None else None
        task._error_message = (
            str(t_row["error_message"]) if t_row["error_message"] is not None else None
        )
        task._retry_count = int(t_row["retry_count"])
        tasks[task.id] = task

    agg = _init_aggregate_slots(
        ScreeningBatch,
        UUID(str(row["batch_id"])),
        tenant_id,
        VersionNumber(int(row["version"])),
    )
    agg._requisition_id = UUID(str(row["requisition_id"]))
    agg._batch_label = str(row["batch_label"])
    agg._tasks = tasks
    agg._seen_hashes = set(json.loads(str(row["seen_hashes_json"])))
    agg._total_files = int(row["total_files"])
    agg._status = BatchStatus(str(row["status"]))
    agg._completed_count = int(row["completed_count"])
    agg._failed_count = int(row["failed_count"])
    agg._pending_count = int(row["pending_count"])
    return agg


# ---------------------------------------------------------------------------
# CandidateProfile & CandidateProfileReadDTO
# ---------------------------------------------------------------------------


def candidate_profile_to_row(profile: CandidateProfile) -> dict[str, object]:
    """Serialize zero-PII CandidateProfile aggregate into SQL column parameters."""
    exps_payload = [
        {
            "entry_id": str(exp.id),
            "role_title": exp.role_title,
            "normalized_role": exp.normalized_role,
            "company_token": exp.company_token,
            "start_year": exp.start_year,
            "start_month": exp.start_month,
            "end_year": exp.end_year,
            "end_month": exp.end_month,
            "is_current": exp.is_current,
            "employment_type": exp.employment_type,
        }
        for exp in profile.work_experiences
    ]
    projects_payload = [
        {
            "project_id": str(proj.id),
            "project_name": proj.project_name,
            "scale_tier": proj.scale_tier,
            "summary": proj.summary,
            "source_quote": proj.source_quote,
            "linked_work_entry_id": (
                str(proj.linked_work_entry_id) if proj.linked_work_entry_id is not None else None
            ),
            "impact_metrics": [
                {
                    "metric_type": m.metric_type,
                    "numeric_value": m.numeric_value,
                    "unit": m.unit,
                    "source_quote": m.source_quote,
                }
                for m in proj.impact_metrics
            ],
        }
        for proj in profile.projects
    ]
    claims_payload = [
        {
            "canonical_skill_id": c.canonical_skill_id,
            "raw_label": c.raw_label,
            "source_quote": c.source_quote,
            "claimed_depth": c.claimed_depth,
            "applied_months": c.applied_months,
            "recency_months": c.recency_months,
            "confidence": float(c.confidence),
        }
        for c in profile.skill_claims
    ]
    seniority = profile.seniority_estimate
    return {
        "profile_id": str(profile.id),
        "tenant_id": str(profile.tenant_id.value),
        "anonymized_token": str(profile.anonymized_token),
        "file_sha256_hex": profile.file_hash.sha256.value,
        "file_size_bytes": profile.file_hash.file_size_bytes,
        "mime_type": profile.file_hash.mime_type,
        "pii_vault_ref_id": str(profile.pii_redaction_map.vault_reference_id),
        "token_categories_json": _json_dumps(
            [[tok, cat] for tok, cat in profile.pii_redaction_map.token_categories]
        ),
        "work_experiences_json": _json_dumps(exps_payload),
        "projects_json": _json_dumps(projects_payload),
        "skill_claims_json": _json_dumps(claims_payload),
        "seniority_level": seniority.level if seniority is not None else None,
        "verified_experience_months": (
            seniority.verified_experience_months if seniority is not None else None
        ),
        "seniority_confidence": float(seniority.confidence) if seniority is not None else None,
        "version": int(profile.version),
    }


def _hydrate_work_experiences(raw_json: str) -> tuple[WorkExperienceEntry, ...]:
    items = json.loads(raw_json)
    return tuple(
        WorkExperienceEntry(
            entry_id=UUID(str(item["entry_id"])),
            role_title=str(item["role_title"]),
            normalized_role=str(item["normalized_role"]),
            company_token=str(item["company_token"]),
            start_year=int(item["start_year"]),
            start_month=int(item["start_month"]),
            end_year=int(item["end_year"]) if item["end_year"] is not None else None,
            end_month=int(item["end_month"]) if item["end_month"] is not None else None,
            is_current=bool(item["is_current"]),
            employment_type=str(item["employment_type"]),
        )
        for item in items
    )


def _hydrate_projects(raw_json: str) -> tuple[ProjectComplexityRecord, ...]:
    items = json.loads(raw_json)
    return tuple(
        ProjectComplexityRecord(
            project_id=UUID(str(item["project_id"])),
            project_name=str(item["project_name"]),
            scale_tier=str(item["scale_tier"]),
            summary=str(item["summary"]),
            source_quote=str(item["source_quote"]),
            linked_work_entry_id=(
                UUID(str(item["linked_work_entry_id"]))
                if item["linked_work_entry_id"] is not None
                else None
            ),
            impact_metrics=tuple(
                ImpactMetric(
                    metric_type=str(m["metric_type"]),
                    numeric_value=float(m["numeric_value"]),
                    unit=str(m["unit"]),
                    source_quote=str(m["source_quote"]),
                )
                for m in item["impact_metrics"]
            ),
        )
        for item in items
    )


def _hydrate_skill_claims(raw_json: str) -> tuple[ExtractedSkillClaim, ...]:
    items = json.loads(raw_json)
    return tuple(
        ExtractedSkillClaim(
            canonical_skill_id=str(item["canonical_skill_id"]),
            raw_label=str(item["raw_label"]),
            source_quote=str(item["source_quote"]),
            claimed_depth=int(item["claimed_depth"]),
            applied_months=int(item["applied_months"]),
            recency_months=int(item["recency_months"]),
            confidence=ConfidenceScore(float(item["confidence"])),
        )
        for item in items
    )


def row_to_candidate_profile(row: sqlite3.Row) -> CandidateProfile:
    """Hydrate zero-PII CandidateProfile aggregate from a SQL row."""
    tenant_id = TenantId.from_str(str(row["tenant_id"]))
    file_hash = FileContentHash(
        sha256=SHA256Hash(str(row["file_sha256_hex"])),
        file_size_bytes=int(row["file_size_bytes"]),
        mime_type=str(row["mime_type"]),
    )
    redaction_map = PiiRedactionMap(
        vault_reference_id=UUID(str(row["pii_vault_ref_id"])),
        token_categories=tuple(
            (str(tok), str(cat)) for tok, cat in json.loads(str(row["token_categories_json"]))
        ),
    )
    seniority = SeniorityEstimate(
        level=(
            str(row["seniority_level"]) if row["seniority_level"] is not None else "MID"
        ),
        verified_experience_months=(
            int(row["verified_experience_months"])
            if row["verified_experience_months"] is not None
            else 0
        ),
        confidence=ConfidenceScore(
            float(row["seniority_confidence"])
            if row["seniority_confidence"] is not None
            else 0.80
        ),
    )

    agg = _init_aggregate_slots(
        CandidateProfile,
        UUID(str(row["profile_id"])),
        tenant_id,
        VersionNumber(int(row["version"])),
    )
    agg._anonymized_token = UUID(str(row["anonymized_token"]))
    agg._file_hash = file_hash
    agg._pii_redaction_map = redaction_map
    agg._work_experiences = _hydrate_work_experiences(str(row["work_experiences_json"]))
    agg._projects = _hydrate_projects(str(row["projects_json"]))
    agg._skill_claims = _hydrate_skill_claims(str(row["skill_claims_json"]))
    agg._seniority_estimate = seniority
    return agg


def row_to_candidate_profile_read_dto(row: sqlite3.Row) -> CandidateProfileReadDTO:
    """Project SQL row directly into CQRS zero-PII CandidateProfileReadDTO."""
    claims = json.loads(str(row["skill_claims_json"]))
    projects = json.loads(str(row["projects_json"]))
    return CandidateProfileReadDTO(
        profile_id=UUID(str(row["profile_id"])),
        tenant_id=TenantId.from_str(str(row["tenant_id"])),
        anonymized_token=UUID(str(row["anonymized_token"])),
        verified_experience_months=(
            int(row["verified_experience_months"])
            if row["verified_experience_months"] is not None
            else 0
        ),
        seniority_level=(
            str(row["seniority_level"]) if row["seniority_level"] is not None else "MID"
        ),
        skill_ids=tuple(str(c["canonical_skill_id"]) for c in claims),
        project_summaries=tuple(str(p["summary"]) for p in projects),
    )


# ---------------------------------------------------------------------------
# SemanticMatchResult
# ---------------------------------------------------------------------------


def semantic_match_to_row(match: SemanticMatchResult) -> dict[str, object]:
    """Serialize SemanticMatchResult aggregate into SQL column parameters."""
    items_payload = [
        {
            "item_id": str(item.id),
            "competency_id": str(item.competency_id),
            "canonical_skill_id": item.canonical_skill_id,
            "weight": item.weight,
            "match_vector": _serialize_hybrid_vector(item.match_vector),
            "achieved_score": item.achieved_score,
            "evidence_excerpts": list(item.evidence_excerpts),
        }
        for item in match.alignment_items
    ]
    gaps_payload = [
        {
            "competency_id": str(gap.competency_id),
            "canonical_skill_id": gap.canonical_skill_id,
            "gap_type": gap.gap_type.value,
            "probe_priority": gap.probe_priority,
            "suspect_claim_summary": gap.suspect_claim_summary,
        }
        for gap in match.skill_gaps
    ]
    return {
        "match_result_id": str(match.id),
        "tenant_id": str(match.tenant_id.value),
        "requisition_id": str(match.requisition_id),
        "candidate_profile_id": str(match.candidate_profile_id),
        "rubric_version": int(match.rubric_version),
        "match_vector_json": _json_dumps(_serialize_hybrid_vector(match.match_vector)),
        "alignment_items_json": _json_dumps(items_payload),
        "skill_gaps_json": _json_dumps(gaps_payload),
        "tier": match.tier.value,
        "failed_gatekeepers_json": _json_dumps(list(match.failed_gatekeepers)),
        "version": int(match.version),
    }


def row_to_semantic_match(row: sqlite3.Row) -> SemanticMatchResult:
    """Hydrate SemanticMatchResult aggregate losslessly from a SQL row."""
    tenant_id = TenantId.from_str(str(row["tenant_id"]))
    items = tuple(
        CompetencyAlignmentItem(
            item_id=UUID(str(i["item_id"])),
            competency_id=UUID(str(i["competency_id"])),
            canonical_skill_id=str(i["canonical_skill_id"]),
            weight=float(i["weight"]),
            match_vector=_deserialize_hybrid_vector(i["match_vector"]),
            achieved_score=float(i["achieved_score"]),
            evidence_excerpts=tuple(str(ex) for ex in i["evidence_excerpts"]),
        )
        for i in json.loads(str(row["alignment_items_json"]))
    )
    gaps = tuple(
        EpistemicSkillGap(
            competency_id=UUID(str(g["competency_id"])),
            canonical_skill_id=str(g["canonical_skill_id"]),
            gap_type=GapType(str(g["gap_type"])),
            probe_priority=float(g["probe_priority"]),
            suspect_claim_summary=str(g["suspect_claim_summary"]),
        )
        for g in json.loads(str(row["skill_gaps_json"]))
    )
    agg = _init_aggregate_slots(
        SemanticMatchResult,
        UUID(str(row["match_result_id"])),
        tenant_id,
        VersionNumber(int(row["version"])),
    )
    agg._requisition_id = UUID(str(row["requisition_id"]))
    agg._candidate_profile_id = UUID(str(row["candidate_profile_id"]))
    agg._rubric_version = VersionNumber(int(row["rubric_version"]))
    agg._match_vector = _deserialize_hybrid_vector(json.loads(str(row["match_vector_json"])))
    agg._alignment_items = items
    agg._skill_gaps = gaps
    agg._tier = CandidateTier(str(row["tier"]))
    agg._failed_gatekeepers = tuple(
        str(x) for x in json.loads(str(row["failed_gatekeepers_json"]))
    )
    return agg


# ---------------------------------------------------------------------------
# InterviewSession & AdaptiveTurn
# ---------------------------------------------------------------------------


def _serialize_latency(metric: ResponseLatencyMetric | None) -> dict[str, object] | None:
    if metric is None:
        return None
    return {
        "ttft_ms": metric.ttft_ms,
        "total_duration_ms": metric.total_duration_ms,
        "speech_cadence_wpm": metric.speech_cadence_wpm,
        "pause_entropy": metric.pause_entropy,
    }


def _deserialize_latency(data: Mapping[str, Any] | None) -> ResponseLatencyMetric | None:
    if data is None:
        return None
    return ResponseLatencyMetric(
        ttft_ms=int(data["ttft_ms"]),
        total_duration_ms=int(data["total_duration_ms"]),
        speech_cadence_wpm=float(data["speech_cadence_wpm"]),
        pause_entropy=float(data["pause_entropy"]),
    )


def interview_session_to_rows(
    session: InterviewSession,
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    """Serialize InterviewSession aggregate and child AdaptiveTurn entities."""
    telemetry_payload = [
        {
            "snapshot_id": str(snap.id),
            "turn_number": snap.turn_number,
            "focus_loss_count": snap.focus_loss_count,
            "focus_loss_duration_ms": snap.focus_loss_duration_ms,
            "external_paste_detected": snap.external_paste_detected,
            "paste_char_count": snap.paste_char_count,
            "keystroke_anomaly_detected": snap.keystroke_anomaly_detected,
            "response_latency": _serialize_latency(snap.response_latency),
            "captured_at": snap.captured_at.isoformat(),
        }
        for snap in session.telemetry_snapshots
    ]
    parent_row: dict[str, object] = {
        "session_id": str(session.id),
        "tenant_id": str(session.tenant_id.value),
        "requisition_id": str(session.requisition_id),
        "candidate_profile_id": str(session.candidate_profile_id),
        "rubric_version": int(session.rubric_version),
        "competency_queue_json": _json_dumps([str(cid) for cid in session.competency_queue]),
        "active_competency_id": (
            str(session.active_competency_id)
            if session.active_competency_id is not None
            else None
        ),
        "current_difficulty": int(session.current_difficulty),
        "max_turns": session.max_turns,
        "token_budget_limit": session.token_budget_limit,
        "tokens_consumed": session.tokens_consumed,
        "status": session.status.value,
        "telemetry_snapshots_json": _json_dumps(telemetry_payload),
        "integrity_flags_json": _json_dumps(
            [_serialize_integrity_flag(f) for f in session.integrity_flags]
        ),
        "probe_counts_json": _json_dumps(
            {str(k): v for k, v in session.probe_counts_by_competency.items()}
        ),
        "termination_reason": session.termination_reason,
        "version": int(session.version),
    }

    turn_rows: list[dict[str, object]] = []
    for turn in session.turns:
        ev = turn.evaluation
        probe = turn.socratic_probe
        turn_rows.append(
            {
                "turn_id": str(turn.id),
                "session_id": str(session.id),
                "tenant_id": str(session.tenant_id.value),
                "turn_number": turn.turn_number,
                "competency_id": str(turn.competency_id),
                "difficulty": int(turn.difficulty),
                "question_prompt": turn.question_prompt,
                "prompt_hash_sha256": turn.prompt_hash.sha256.value,
                "prompt_template_version": turn.prompt_hash.template_version,
                "candidate_response": turn.candidate_response,
                "evaluation_json": _json_dumps(
                    {
                        "technical_accuracy": ev.technical_accuracy,
                        "depth_of_understanding": ev.depth_of_understanding,
                        "tradeoff_awareness": ev.tradeoff_awareness,
                        "contextual_specificity": ev.contextual_specificity,
                        "suspicion_score": ev.suspicion_score,
                    }
                ),
                "tokens_used": turn.tokens_used,
                "socratic_probe_json": (
                    _json_dumps(
                        {
                            "probe_id": str(probe.id),
                            "competency_id": str(probe.competency_id),
                            "probe_index": probe.probe_index,
                            "strategy": probe.strategy,
                            "target_concept": probe.target_concept,
                            "probe_prompt": probe.probe_prompt,
                            "prompt_hash_sha256": probe.prompt_hash.sha256.value,
                            "prompt_template_version": probe.prompt_hash.template_version,
                        }
                    )
                    if probe is not None
                    else None
                ),
                "latency_metric_json": (
                    _json_dumps(_serialize_latency(turn.latency_metric))
                    if turn.latency_metric is not None
                    else None
                ),
                "integrity_flags_json": _json_dumps(
                    [_serialize_integrity_flag(f) for f in turn.integrity_flags]
                ),
            }
        )
    return parent_row, tuple(turn_rows)


def row_to_interview_session(
    row: sqlite3.Row,
    turn_rows: tuple[sqlite3.Row, ...],
) -> InterviewSession:
    """Hydrate InterviewSession aggregate and child AdaptiveTurn entities."""
    tenant_id = TenantId.from_str(str(row["tenant_id"]))
    turns: list[AdaptiveTurn] = []
    for t_row in turn_rows:
        ev_data = json.loads(str(t_row["evaluation_json"]))
        probe_raw = (
            json.loads(str(t_row["socratic_probe_json"]))
            if t_row["socratic_probe_json"] is not None
            else None
        )
        probe = (
            SocraticProbe(
                probe_id=UUID(str(probe_raw["probe_id"])),
                competency_id=UUID(str(probe_raw["competency_id"])),
                probe_index=int(probe_raw["probe_index"]),
                strategy=str(probe_raw["strategy"]),
                target_concept=str(probe_raw["target_concept"]),
                probe_prompt=str(probe_raw["probe_prompt"]),
                prompt_hash=PromptHash(
                    sha256=SHA256Hash(str(probe_raw["prompt_hash_sha256"])),
                    template_version=str(probe_raw["prompt_template_version"]),
                ),
            )
            if probe_raw is not None
            else None
        )
        lat_raw = (
            json.loads(str(t_row["latency_metric_json"]))
            if t_row["latency_metric_json"] is not None
            else None
        )
        turns.append(
            AdaptiveTurn(
                turn_id=UUID(str(t_row["turn_id"])),
                turn_number=int(t_row["turn_number"]),
                competency_id=UUID(str(t_row["competency_id"])),
                difficulty=DifficultyLevel.from_int(int(t_row["difficulty"])),
                question_prompt=str(t_row["question_prompt"]),
                prompt_hash=PromptHash(
                    sha256=SHA256Hash(str(t_row["prompt_hash_sha256"])),
                    template_version=str(t_row["prompt_template_version"]),
                ),
                candidate_response=str(t_row["candidate_response"]),
                evaluation=TurnEvaluationScore(
                    technical_accuracy=float(ev_data["technical_accuracy"]),
                    depth_of_understanding=float(ev_data["depth_of_understanding"]),
                    tradeoff_awareness=float(ev_data["tradeoff_awareness"]),
                    contextual_specificity=float(ev_data["contextual_specificity"]),
                    suspicion_score=float(ev_data["suspicion_score"]),
                ),
                tokens_used=int(t_row["tokens_used"]),
                socratic_probe=probe,
                latency_metric=_deserialize_latency(lat_raw),
                integrity_flags=tuple(
                    _deserialize_integrity_flag(f)
                    for f in json.loads(str(t_row["integrity_flags_json"]))
                ),
            )
        )

    telemetry_snapshots = [
        TelemetrySnapshot(
            snapshot_id=UUID(str(s["snapshot_id"])),
            turn_number=int(s["turn_number"]),
            focus_loss_count=int(s["focus_loss_count"]),
            focus_loss_duration_ms=int(s["focus_loss_duration_ms"]),
            external_paste_detected=bool(s["external_paste_detected"]),
            paste_char_count=int(s["paste_char_count"]),
            keystroke_anomaly_detected=bool(s["keystroke_anomaly_detected"]),
            response_latency=_deserialize_latency(s["response_latency"]),
            captured_at=_parse_ts(str(s["captured_at"])),
        )
        for s in json.loads(str(row["telemetry_snapshots_json"]))
    ]
    flags = [
        _deserialize_integrity_flag(f) for f in json.loads(str(row["integrity_flags_json"]))
    ]
    probe_counts = {
        UUID(k): int(v) for k, v in json.loads(str(row["probe_counts_json"])).items()
    }

    agg = _init_aggregate_slots(
        InterviewSession,
        UUID(str(row["session_id"])),
        tenant_id,
        VersionNumber(int(row["version"])),
    )
    agg._requisition_id = UUID(str(row["requisition_id"]))
    agg._candidate_profile_id = UUID(str(row["candidate_profile_id"]))
    agg._rubric_version = VersionNumber(int(row["rubric_version"]))
    agg._competency_queue = tuple(
        UUID(cid) for cid in json.loads(str(row["competency_queue_json"]))
    )
    agg._active_competency_id = (
        UUID(str(row["active_competency_id"]))
        if row["active_competency_id"] is not None
        else None
    )
    agg._current_difficulty = DifficultyLevel.from_int(int(row["current_difficulty"]))
    agg._max_turns = int(row["max_turns"])
    agg._token_budget_limit = int(row["token_budget_limit"])
    agg._tokens_consumed = int(row["tokens_consumed"])
    agg._status = SessionStatus(str(row["status"]))
    agg._turns = turns
    agg._telemetry_snapshots = telemetry_snapshots
    agg._integrity_flags = flags
    agg._probe_counts_by_competency = probe_counts
    agg._termination_reason = (
        str(row["termination_reason"]) if row["termination_reason"] is not None else None
    )
    return agg


# ---------------------------------------------------------------------------
# EvaluationReport & RecruiterOverrideAudit
# ---------------------------------------------------------------------------


def evaluation_report_to_rows(
    report: EvaluationReport,
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    """Serialize EvaluationReport aggregate and child RecruiterOverrideAudit rows."""
    scorecard_payload = [
        {
            "item_id": str(item.id),
            "competency_id": str(item.competency_id),
            "competency_name": item.competency_name,
            "weight": item.weight,
            "target_depth": item.target_depth,
            "achieved_depth": item.achieved_depth,
            "normalized_score": item.normalized_score,
            "evidence_turn_numbers": list(item.evidence_turn_numbers),
            "evidence_quotes": list(item.evidence_quotes),
            "rationale": item.rationale,
        }
        for item in report.scorecard_items
    ]
    comp = report.composite_score
    auth = report.authenticity_index
    ci = auth.confidence_interval
    integrity_payload = [
        {
            "entry_id": str(entry.id),
            "turn_number": entry.turn_number,
            "flag_code": entry.flag_code,
            "severity": entry.severity,
            "suspicion_risk": entry.suspicion_risk,
            "description": entry.description,
        }
        for entry in report.integrity_entries
    ]
    parent_row: dict[str, object] = {
        "report_id": str(report.id),
        "tenant_id": str(report.tenant_id.value),
        "interview_session_id": str(report.interview_session_id),
        "requisition_id": str(report.requisition_id),
        "candidate_profile_id": str(report.candidate_profile_id),
        "scorecard_items_json": _json_dumps(scorecard_payload),
        "composite_score_json": _json_dumps(
            {
                "technical_interview_score": comp.technical_interview_score,
                "cv_match_score": comp.cv_match_score,
                "authenticity_moderator": comp.authenticity_moderator,
                "recommendation_index": comp.recommendation_index,
            }
        ),
        "authenticity_index_json": _json_dumps(
            {
                "score": auth.score,
                "ci_lower": ci.lower if ci is not None else None,
                "ci_upper": ci.upper if ci is not None else None,
            }
        ),
        "initial_band": report.initial_recommendation_band.value,
        "effective_band": report.effective_recommendation_band.value,
        "executive_summary": report.executive_summary,
        "integrity_entries_json": _json_dumps(integrity_payload),
        "valid_turn_numbers_json": (
            _json_dumps(sorted(report._valid_turn_numbers))
            if report._valid_turn_numbers is not None
            else None
        ),
        "is_finalized": int(report.is_finalized),
        "report_hash_hex": report.report_hash.value if report.report_hash is not None else None,
        "version": int(report.version),
    }
    override_rows: tuple[dict[str, object], ...] = tuple(
        {
            "override_id": str(ov.id),
            "report_id": str(report.id),
            "tenant_id": str(report.tenant_id.value),
            "sort_order": idx,
            "actor_id": str(ov.actor_id),
            "previous_band": ov.previous_band.value,
            "new_band": ov.new_band.value,
            "justification": ov.justification,
            "recorded_at": ov.recorded_at.isoformat(),
        }
        for idx, ov in enumerate(report.override_audits)
    )
    return parent_row, override_rows


def row_to_evaluation_report(
    row: sqlite3.Row,
    override_rows: tuple[sqlite3.Row, ...],
) -> EvaluationReport:
    """Hydrate EvaluationReport aggregate and child RecruiterOverrideAudit entities."""
    tenant_id = TenantId.from_str(str(row["tenant_id"]))
    scorecard_items = tuple(
        CompetencyScorecardItem(
            item_id=UUID(str(i["item_id"])),
            competency_id=UUID(str(i["competency_id"])),
            competency_name=str(i["competency_name"]),
            weight=float(i["weight"]),
            target_depth=int(i["target_depth"]),
            achieved_depth=float(i["achieved_depth"]),
            normalized_score=float(i["normalized_score"]),
            evidence_turn_numbers=tuple(int(t) for t in i["evidence_turn_numbers"]),
            evidence_quotes=tuple(str(q) for q in i["evidence_quotes"]),
            rationale=str(i["rationale"]),
        )
        for i in json.loads(str(row["scorecard_items_json"]))
    )
    comp_data = json.loads(str(row["composite_score_json"]))
    composite_score = CompositeRubricScore(
        technical_interview_score=float(comp_data["technical_interview_score"]),
        cv_match_score=float(comp_data["cv_match_score"]),
        authenticity_moderator=float(comp_data["authenticity_moderator"]),
        recommendation_index=float(comp_data["recommendation_index"]),
    )
    auth_data = json.loads(str(row["authenticity_index_json"]))
    ci = (
        ConfidenceInterval(lower=float(auth_data["ci_lower"]), upper=float(auth_data["ci_upper"]))
        if auth_data["ci_lower"] is not None and auth_data["ci_upper"] is not None
        else None
    )
    auth_index = AuthenticityIndex(score=float(auth_data["score"]), confidence_interval=ci)
    integrity_entries = tuple(
        IntegrityAuditEntry(
            entry_id=UUID(str(e["entry_id"])),
            turn_number=int(e["turn_number"]),
            flag_code=str(e["flag_code"]),
            severity=str(e["severity"]),
            suspicion_risk=float(e["suspicion_risk"]),
            description=str(e["description"]),
        )
        for e in json.loads(str(row["integrity_entries_json"]))
    )
    overrides = [
        RecruiterOverrideAudit(
            override_id=UUID(str(o_row["override_id"])),
            actor_id=UUID(str(o_row["actor_id"])),
            previous_band=HiringRecommendationBand(str(o_row["previous_band"])),
            new_band=HiringRecommendationBand(str(o_row["new_band"])),
            justification=str(o_row["justification"]),
            recorded_at=_parse_ts(str(o_row["recorded_at"])),
        )
        for o_row in override_rows
    ]
    valid_turns = (
        frozenset(int(t) for t in json.loads(str(row["valid_turn_numbers_json"])))
        if row["valid_turn_numbers_json"] is not None
        else None
    )

    agg = _init_aggregate_slots(
        EvaluationReport,
        UUID(str(row["report_id"])),
        tenant_id,
        VersionNumber(int(row["version"])),
    )
    agg._interview_session_id = UUID(str(row["interview_session_id"]))
    agg._requisition_id = UUID(str(row["requisition_id"]))
    agg._candidate_profile_id = UUID(str(row["candidate_profile_id"]))
    agg._scorecard_items = scorecard_items
    agg._composite_score = composite_score
    agg._authenticity_index = auth_index
    agg._initial_band = HiringRecommendationBand(str(row["initial_band"]))
    agg._effective_band = HiringRecommendationBand(str(row["effective_band"]))
    agg._executive_summary = str(row["executive_summary"])
    agg._integrity_entries = integrity_entries
    agg._valid_turn_numbers = valid_turns
    agg._override_audits = overrides
    agg._is_finalized = bool(row["is_finalized"])
    agg._report_hash = (
        SHA256Hash(str(row["report_hash_hex"])) if row["report_hash_hex"] is not None else None
    )
    return agg
