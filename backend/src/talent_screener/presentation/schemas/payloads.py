"""Presentation Wire Payload Validators & Translators into Immutable Application DTOs."""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence
from enum import Enum
from uuid import UUID

from talent_screener.application.document_intelligence.dtos import (
    BatchFileUploadSpecDTO,
    ExecuteHybridMatchingRequestDTO,
    InitiateScreeningBatchRequestDTO,
)
from talent_screener.application.evaluation_analytics.dtos import (
    BuildCandidateComparisonMatrixRequestDTO,
    GenerateEvaluationReportRequestDTO,
    RecordRecruiterOverrideRequestDTO,
)
from talent_screener.application.identity_governance.dtos import (
    ExecuteCandidateErasureRequestDTO,
    ProvisionTenantAccountRequestDTO,
    RecordCandidateConsentRequestDTO,
    RevokeCandidateConsentRequestDTO,
)
from talent_screener.application.live_interview.dtos import (
    CompleteInterviewSessionRequestDTO,
    InitializeInterviewSessionRequestDTO,
    RecordClientTelemetryRequestDTO,
    TerminateInterviewEarlyRequestDTO,
)
from talent_screener.application.recruitment.dtos import (
    CompetencyCriterionInputDTO,
    CreateJobRequisitionRequestDTO,
    TierThresholdsDTO,
    UpdateJobRubricRequestDTO,
)
from talent_screener.domain.contexts.evaluation_analytics.value_objects import (
    HiringRecommendationBand,
)
from talent_screener.domain.contexts.identity_governance.value_objects import ConsentScope
from talent_screener.domain.contexts.live_interview.value_objects import DifficultyLevel
from talent_screener.domain.contexts.recruitment.value_objects import SeniorityLevel
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.presentation.exceptions import (
    FieldViolation,
    RequestValidationError,
)

MAX_SCREENING_BATCH_FILES = 500


def _fail_field(field_path: str, constraint: str, reason: str) -> RequestValidationError:
    return RequestValidationError(
        f"Invalid request payload field '{field_path}': {reason}",
        errors=(FieldViolation(field_path=field_path, constraint=constraint, reason=reason),),
    )


def require_body_mapping(body: Mapping[str, object] | None) -> Mapping[str, object]:
    """Ensure incoming body is a JSON object mapping."""
    if body is None or not isinstance(body, Mapping):
        raise _fail_field("body", "type:object", "Request body must be a JSON object.")
    return body


def parse_uuid(value: object, *, field_name: str) -> UUID:
    """Parse strict UUID string or UUID value."""
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value.strip())
        except ValueError as exc:
            raise _fail_field(field_name, "format:uuid", f"Invalid UUID: {value!r}.") from exc
    raise _fail_field(field_name, "format:uuid", "Expected valid UUID string.")


def parse_str(
    body: Mapping[str, object],
    key: str,
    *,
    required: bool = True,
    default: str = "",
) -> str:
    """Extract and validate non-empty string field."""
    raw = body.get(key)
    if raw is None:
        if required:
            raise _fail_field(key, "required", f"Missing required field '{key}'.")
        return default
    if not isinstance(raw, str):
        raise _fail_field(key, "type:string", f"Field '{key}' must be a string.")
    cleaned = raw.strip()
    if required and not cleaned:
        raise _fail_field(key, "non_empty", f"Field '{key}' cannot be empty.")
    return cleaned


def parse_int(
    body: Mapping[str, object],
    key: str,
    *,
    required: bool = False,
    default: int = 0,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Extract strict integer (rejecting booleans) with optional range bounds."""
    raw = body.get(key)
    if raw is None:
        if required:
            raise _fail_field(key, "required", f"Missing required integer field '{key}'.")
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _fail_field(key, "type:integer", f"Field '{key}' must be an integer.")
    if minimum is not None and raw < minimum:
        raise _fail_field(key, f"min:{minimum}", f"Field '{key}' must be >= {minimum}.")
    if maximum is not None and raw > maximum:
        raise _fail_field(key, f"max:{maximum}", f"Field '{key}' must be <= {maximum}.")
    return raw


def parse_float(
    body: Mapping[str, object],
    key: str,
    *,
    required: bool = False,
    default: float = 0.0,
) -> float:
    """Extract numeric float value (rejecting booleans)."""
    raw = body.get(key)
    if raw is None:
        if required:
            raise _fail_field(key, "required", f"Missing required number field '{key}'.")
        return default
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise _fail_field(key, "type:number", f"Field '{key}' must be numeric.")
    return float(raw)


def parse_bool(
    body: Mapping[str, object],
    key: str,
    *,
    default: bool = False,
) -> bool:
    """Extract strict boolean value."""
    raw = body.get(key)
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise _fail_field(key, "type:boolean", f"Field '{key}' must be boolean.")
    return raw


def parse_enum[E: Enum](
    raw: object,
    enum_cls: type[E],
    *,
    field_name: str,
) -> E:
    """Parse Enum value strictly from wire payload (supports canonical value or member name)."""
    try:
        if isinstance(raw, enum_cls):
            return raw
        if isinstance(raw, str | int):
            return enum_cls(raw)
    except ValueError:
        if isinstance(raw, str):
            member_key = raw.strip().upper()
            if member_key in enum_cls.__members__:
                return enum_cls[member_key]
    allowed = [item.value for item in enum_cls]
    raise _fail_field(field_name, "enum", f"Expected one of {allowed}; got {raw!r}.")


def _parse_criteria_list(raw_list: object) -> tuple[CompetencyCriterionInputDTO, ...]:
    if not isinstance(raw_list, Sequence) or isinstance(raw_list, str | bytes) or not raw_list:
        raise _fail_field("criteria", "min_items:1", "'criteria' must be a non-empty list.")
    parsed: list[CompetencyCriterionInputDTO] = []
    for idx, item in enumerate(raw_list):
        if not isinstance(item, Mapping):
            raise _fail_field(f"criteria[{idx}]", "type:object", "Criterion must be an object.")
        parsed.append(
            CompetencyCriterionInputDTO(
                name=parse_str(item, "name"),
                canonical_skill_id=parse_str(item, "canonical_skill_id"),
                weight=parse_float(item, "weight", required=True),
                target_depth=parse_int(item, "target_depth", required=True, minimum=1, maximum=5),
                is_gatekeeper=parse_bool(item, "is_gatekeeper", default=False),
                raw_importance=parse_float(item, "raw_importance", default=1.0),
                ideal_anchor_descriptor=parse_str(
                    item, "ideal_anchor_descriptor", required=False, default=""
                ),
                min_applied_months=parse_int(item, "min_applied_months", default=0, minimum=0),
                strict_disqualification=parse_bool(item, "strict_disqualification", default=True),
            )
        )
    return tuple(parsed)


def _parse_thresholds(raw: object) -> TierThresholdsDTO | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise _fail_field("tier_thresholds", "type:object", "'tier_thresholds' must be an object.")
    return TierThresholdsDTO(
        fast_track_threshold=parse_float(raw, "fast_track_threshold", default=88.0),
        interview_threshold=parse_float(raw, "interview_threshold", default=75.0),
        review_threshold=parse_float(raw, "review_threshold", default=58.0),
    )


def parse_create_requisition(
    body: Mapping[str, object] | None, *, tenant_id: TenantId
) -> CreateJobRequisitionRequestDTO:
    """Validate HTTP JSON body and construct CreateJobRequisitionRequestDTO."""
    data = require_body_mapping(body)
    return CreateJobRequisitionRequestDTO(
        tenant_id=tenant_id,
        title=parse_str(data, "title"),
        description=parse_str(data, "description"),
        seniority_level=parse_enum(
            data.get("seniority_level"), SeniorityLevel, field_name="seniority_level"
        ),
        criteria=_parse_criteria_list(data.get("criteria")),
        tier_thresholds=_parse_thresholds(data.get("tier_thresholds")),
        interview_timebox_minutes=parse_int(
            data, "interview_timebox_minutes", default=30, minimum=10, maximum=120
        ),
    )


def parse_update_rubric(
    body: Mapping[str, object] | None, *, tenant_id: TenantId, requisition_id: UUID
) -> UpdateJobRubricRequestDTO:
    """Validate HTTP JSON body and construct UpdateJobRubricRequestDTO."""
    data = require_body_mapping(body)
    return UpdateJobRubricRequestDTO(
        tenant_id=tenant_id,
        requisition_id=requisition_id,
        criteria=_parse_criteria_list(data.get("criteria")),
        tier_thresholds=_parse_thresholds(data.get("tier_thresholds")),
    )


def _sanitize_manifest_filename(filename: str, index: int) -> str:
    if ".." in filename or "/" in filename or "\\" in filename:
        raise _fail_field(
            f"files[{index}].filename",
            "path_traversal",
            "Filename must not contain directory traversal or path separators.",
        )
    safe = posixpath.basename(filename.strip())
    if not safe or len(safe) > 255:
        raise _fail_field(
            f"files[{index}].filename",
            "length:1..255",
            "Filename must be between 1 and 255 characters.",
        )
    return safe


def parse_initiate_batch(
    body: Mapping[str, object] | None,
    *,
    tenant_id: TenantId,
    header_idempotency_key: str | None,
) -> InitiateScreeningBatchRequestDTO:
    """Validate screening batch manifest (1..500 files) into InitiateScreeningBatchRequestDTO."""
    data = require_body_mapping(body)
    raw_files = data.get("files")
    if not isinstance(raw_files, Sequence) or isinstance(raw_files, str | bytes) or not raw_files:
        raise _fail_field("files", "min_items:1", "'files' manifest must contain 1 to 500 entries.")
    if len(raw_files) > MAX_SCREENING_BATCH_FILES:
        raise _fail_field(
            "files",
            f"max_items:{MAX_SCREENING_BATCH_FILES}",
            f"Batch exceeds maximum limit of {MAX_SCREENING_BATCH_FILES} files per batch.",
        )

    files_spec: list[BatchFileUploadSpecDTO] = []
    for idx, entry in enumerate(raw_files):
        if not isinstance(entry, Mapping):
            raise _fail_field(f"files[{idx}]", "type:object", "File entry must be an object.")
        filename = _sanitize_manifest_filename(parse_str(entry, "filename"), idx)
        files_spec.append(
            BatchFileUploadSpecDTO(
                filename=filename,
                sha256_hex=parse_str(entry, "sha256_hex"),
                file_size_bytes=parse_int(entry, "file_size_bytes", required=True, minimum=1),
                mime_type=parse_str(entry, "mime_type"),
            )
        )

    idem_key = (header_idempotency_key or "").strip() or parse_str(data, "idempotency_key")
    corr_id = parse_str(data, "correlation_id", required=False, default=str(generate_uuid7()))
    return InitiateScreeningBatchRequestDTO(
        tenant_id=tenant_id,
        requisition_id=parse_uuid(data.get("requisition_id"), field_name="requisition_id"),
        batch_label=parse_str(data, "batch_label"),
        files=tuple(files_spec),
        idempotency_key=idem_key,
        correlation_id=corr_id,
    )


def parse_execute_matching(
    body: Mapping[str, object] | None, *, tenant_id: TenantId, requisition_id: UUID
) -> ExecuteHybridMatchingRequestDTO:
    """Validate hybrid match payload into ExecuteHybridMatchingRequestDTO."""
    data = require_body_mapping(body)
    return ExecuteHybridMatchingRequestDTO(
        tenant_id=tenant_id,
        requisition_id=requisition_id,
        candidate_profile_id=parse_uuid(
            data.get("candidate_profile_id"), field_name="candidate_profile_id"
        ),
        alpha_dense=parse_float(data, "alpha_dense", default=0.55),
        beta_sparse=parse_float(data, "beta_sparse", default=0.25),
        gamma_recency=parse_float(data, "gamma_recency", default=0.20),
        half_life_years=parse_float(data, "half_life_years", default=3.0),
    )


def parse_initialize_interview(
    body: Mapping[str, object] | None, *, tenant_id: TenantId
) -> InitializeInterviewSessionRequestDTO:
    """Validate interview initiation payload into InitializeInterviewSessionRequestDTO."""
    data = require_body_mapping(body)
    raw_diff = data.get("initial_difficulty", DifficultyLevel.L3.value)
    return InitializeInterviewSessionRequestDTO(
        tenant_id=tenant_id,
        requisition_id=parse_uuid(data.get("requisition_id"), field_name="requisition_id"),
        candidate_profile_id=parse_uuid(
            data.get("candidate_profile_id"), field_name="candidate_profile_id"
        ),
        max_turns=parse_int(data, "max_turns", default=16, minimum=1, maximum=50),
        token_budget_limit=parse_int(
            data, "token_budget_limit", default=45_000, minimum=100, maximum=1_000_000
        ),
        initial_difficulty=parse_enum(
            raw_diff, DifficultyLevel, field_name="initial_difficulty"
        ),
    )


def parse_record_telemetry(
    body: Mapping[str, object] | None, *, tenant_id: TenantId, session_id: UUID
) -> RecordClientTelemetryRequestDTO:
    """Validate client behavioral telemetry into RecordClientTelemetryRequestDTO."""
    data = require_body_mapping(body)
    return RecordClientTelemetryRequestDTO(
        tenant_id=tenant_id,
        session_id=session_id,
        turn_number=parse_int(data, "turn_number", required=True, minimum=1),
        focus_loss_count=parse_int(data, "focus_loss_count", default=0, minimum=0),
        focus_loss_duration_ms=parse_int(data, "focus_loss_duration_ms", default=0, minimum=0),
        external_paste_detected=parse_bool(data, "external_paste_detected", default=False),
        paste_char_count=parse_int(data, "paste_char_count", default=0, minimum=0),
        keystroke_anomaly_detected=parse_bool(data, "keystroke_anomaly_detected", default=False),
        ttft_ms=parse_int(data, "ttft_ms", default=0, minimum=0),
        total_duration_ms=parse_int(data, "total_duration_ms", default=0, minimum=0),
        speech_cadence_wpm=parse_float(data, "speech_cadence_wpm", default=0.0),
        pause_entropy=parse_float(data, "pause_entropy", default=0.0),
    )


def parse_terminate_interview(
    body: Mapping[str, object] | None, *, tenant_id: TenantId, session_id: UUID
) -> TerminateInterviewEarlyRequestDTO:
    """Validate early termination payload into TerminateInterviewEarlyRequestDTO."""
    data = require_body_mapping(body)
    return TerminateInterviewEarlyRequestDTO(
        tenant_id=tenant_id,
        session_id=session_id,
        reason=parse_str(data, "reason"),
    )


def parse_complete_interview(
    body: Mapping[str, object] | None, *, tenant_id: TenantId, session_id: UUID
) -> CompleteInterviewSessionRequestDTO:
    """Validate completion payload into CompleteInterviewSessionRequestDTO."""
    data = require_body_mapping(body)
    return CompleteInterviewSessionRequestDTO(
        tenant_id=tenant_id,
        session_id=session_id,
        checkpoint_version=parse_str(data, "checkpoint_version"),
    )


def parse_generate_report(
    body: Mapping[str, object] | None, *, tenant_id: TenantId
) -> GenerateEvaluationReportRequestDTO:
    """Validate report generation payload into GenerateEvaluationReportRequestDTO."""
    data = require_body_mapping(body)
    return GenerateEvaluationReportRequestDTO(
        tenant_id=tenant_id,
        interview_session_id=parse_uuid(
            data.get("interview_session_id"), field_name="interview_session_id"
        ),
        executive_summary=parse_str(data, "executive_summary", required=False, default=""),
    )


def parse_recruiter_override(
    body: Mapping[str, object] | None,
    *,
    tenant_id: TenantId,
    report_id: UUID,
    principal_actor_id: UUID,
) -> RecordRecruiterOverrideRequestDTO:
    """Validate recruiter override payload into RecordRecruiterOverrideRequestDTO."""
    data = require_body_mapping(body)
    actor_id = (
        parse_uuid(data["actor_id"], field_name="actor_id")
        if data.get("actor_id") is not None
        else principal_actor_id
    )
    return RecordRecruiterOverrideRequestDTO(
        tenant_id=tenant_id,
        report_id=report_id,
        actor_id=actor_id,
        new_band=parse_enum(
            data.get("new_band"), HiringRecommendationBand, field_name="new_band"
        ),
        justification=parse_str(data, "justification"),
    )


def parse_comparison_matrix(
    body: Mapping[str, object] | None, *, tenant_id: TenantId, requisition_id: UUID
) -> BuildCandidateComparisonMatrixRequestDTO:
    """Validate comparison matrix candidate IDs into BuildCandidateComparisonMatrixRequestDTO."""
    data = require_body_mapping(body)
    raw_ids = data.get("candidate_profile_ids")
    if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, str | bytes):
        raise _fail_field(
            "candidate_profile_ids",
            "type:array",
            "'candidate_profile_ids' must be a list of 2 to 4 candidate UUIDs.",
        )
    cand_ids = tuple(
        parse_uuid(item, field_name=f"candidate_profile_ids[{idx}]")
        for idx, item in enumerate(raw_ids)
    )
    return BuildCandidateComparisonMatrixRequestDTO(
        tenant_id=tenant_id,
        requisition_id=requisition_id,
        candidate_profile_ids=cand_ids,
    )


def parse_provision_tenant(
    body: Mapping[str, object] | None,
    *,
    tenant_id: TenantId | None,
) -> ProvisionTenantAccountRequestDTO:
    """Validate tenant provisioning payload into ProvisionTenantAccountRequestDTO."""
    data = require_body_mapping(body)
    return ProvisionTenantAccountRequestDTO(
        organization_slug=parse_str(data, "organization_slug"),
        display_name=parse_str(data, "display_name"),
        max_concurrent_batches=parse_int(data, "max_concurrent_batches", default=5, minimum=1),
        max_files_per_batch=parse_int(data, "max_files_per_batch", default=500, minimum=1),
        max_monthly_interviews=parse_int(data, "max_monthly_interviews", default=1000, minimum=1),
        max_tokens_per_interview=parse_int(
            data, "max_tokens_per_interview", default=45_000, minimum=100
        ),
        subscription_tier=parse_str(
            data, "subscription_tier", required=False, default="ENTERPRISE"
        ),
        pii_retention_days=parse_int(data, "pii_retention_days", default=180, minimum=1),
        audit_log_retention_days=parse_int(
            data, "audit_log_retention_days", default=2555, minimum=1
        ),
        erasure_sla_hours=parse_int(data, "erasure_sla_hours", default=24, minimum=1),
        tenant_id=tenant_id,
    )


def parse_record_consent(
    body: Mapping[str, object] | None, *, tenant_id: TenantId
) -> RecordCandidateConsentRequestDTO:
    """Validate consent grant payload into RecordCandidateConsentRequestDTO."""
    data = require_body_mapping(body)
    raw_scopes = data.get("granted_scopes")
    if (
        not isinstance(raw_scopes, Sequence)
        or isinstance(raw_scopes, str | bytes)
        or not raw_scopes
    ):
        raise _fail_field(
            "granted_scopes",
            "min_items:1",
            "'granted_scopes' must be a non-empty list of ConsentScope values.",
        )
    scopes = tuple(
        parse_enum(item, ConsentScope, field_name=f"granted_scopes[{idx}]")
        for idx, item in enumerate(raw_scopes)
    )
    return RecordCandidateConsentRequestDTO(
        tenant_id=tenant_id,
        candidate_id=parse_uuid(data.get("candidate_id"), field_name="candidate_id"),
        granted_scopes=scopes,
    )


def parse_revoke_consent(
    *, tenant_id: TenantId, consent_id: UUID
) -> RevokeCandidateConsentRequestDTO:
    """Build RevokeCandidateConsentRequestDTO from path parameter and tenant claim."""
    return RevokeCandidateConsentRequestDTO(tenant_id=tenant_id, record_id=consent_id)


def parse_erasure(
    body: Mapping[str, object] | None, *, tenant_id: TenantId, candidate_id: UUID
) -> ExecuteCandidateErasureRequestDTO:
    """Validate GDPR erasure payload into ExecuteCandidateErasureRequestDTO."""
    reason = "GDPR Article 17 Right to Erasure"
    if body is not None:
        reason = parse_str(body, "reason", required=False, default=reason)
    return ExecuteCandidateErasureRequestDTO(
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        reason=reason,
    )
