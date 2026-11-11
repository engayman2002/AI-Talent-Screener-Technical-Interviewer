"""Use Case Request, Response, and Read DTOs for Document Intelligence & Matching."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.matching.value_objects import (
    CandidateTier,
    GapType,
)
from talent_screener.domain.contexts.document_intelligence.value_objects import (
    BatchStatus,
    TaskIngestionStatus,
)
from talent_screener.domain.shared.value_objects import TenantId


@dataclass(frozen=True, slots=True)
class BatchFileUploadSpecDTO:
    """File metadata descriptor supplied when initiating a ScreeningBatch."""

    filename: str
    sha256_hex: str
    file_size_bytes: int
    mime_type: str


@dataclass(frozen=True, slots=True)
class InitiateScreeningBatchRequestDTO:
    """Command DTO to initiate an idempotent bulk CV ScreeningBatch."""

    tenant_id: TenantId
    requisition_id: UUID
    batch_label: str
    files: tuple[BatchFileUploadSpecDTO, ...]
    idempotency_key: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class PresignedUploadInstructionDTO:
    """Object storage upload instruction generated for one batch document task."""

    task_id: UUID
    filename: str
    object_key: str
    presigned_upload_url: str


@dataclass(frozen=True, slots=True)
class InitiateScreeningBatchResponseDTO:
    """Response DTO returned upon ScreeningBatch initiation or idempotent replay."""

    batch_id: UUID
    requisition_id: UUID
    status: BatchStatus
    total_files: int
    upload_instructions: tuple[PresignedUploadInstructionDTO, ...]
    replayed_from_idempotency: bool = False


@dataclass(frozen=True, slots=True)
class ProcessDocumentIngestionTaskRequestDTO:
    """Command DTO executed by worker or handler for a single CV task."""

    tenant_id: TenantId
    batch_id: UUID
    task_id: UUID
    object_key: str
    correlation_id: str
    skill_taxonomy_version: str = "1.0.0"


@dataclass(frozen=True, slots=True)
class ProcessDocumentIngestionTaskResponseDTO:
    """Outcome DTO after processing or quarantining a CV ingestion task."""

    batch_id: UUID
    task_id: UUID
    task_status: TaskIngestionStatus
    batch_status: BatchStatus
    candidate_profile_id: UUID | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ExecuteHybridMatchingRequestDTO:
    """Command DTO to run hybrid semantic matching between CandidateProfile and JobRequisition."""

    tenant_id: TenantId
    requisition_id: UUID
    candidate_profile_id: UUID
    alpha_dense: float = 0.55
    beta_sparse: float = 0.25
    gamma_recency: float = 0.20
    half_life_years: float = 3.0


@dataclass(frozen=True, slots=True)
class EpistemicSkillGapDTO:
    """Immutable DTO representing a prioritized interview probing gap."""

    competency_id: UUID
    canonical_skill_id: str
    gap_type: GapType
    probe_priority: float
    suspect_claim_summary: str


@dataclass(frozen=True, slots=True)
class HybridMatchResponseDTO:
    """Outcome DTO containing deterministic match score, tier, and skill gaps."""

    match_result_id: UUID
    requisition_id: UUID
    candidate_profile_id: UUID
    composite_score: float
    gatekeeper_passed: bool
    failed_gatekeepers: tuple[str, ...]
    tier: CandidateTier
    skill_gaps: tuple[EpistemicSkillGapDTO, ...]


@dataclass(frozen=True, slots=True)
class GetScreeningBatchStatusQueryDTO:
    """Query DTO for reading ScreeningBatch progress and task states."""

    tenant_id: TenantId
    batch_id: UUID


@dataclass(frozen=True, slots=True)
class TaskStatusReadDTO:
    """Read projection of one DocumentIngestionTask."""

    task_id: UUID
    filename: str
    status: TaskIngestionStatus
    candidate_profile_id: UUID | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ScreeningBatchStatusDTO:
    """Read projection of ScreeningBatch progress."""

    batch_id: UUID
    requisition_id: UUID
    batch_label: str
    status: BatchStatus
    total_files: int
    completed_count: int
    failed_count: int
    pending_count: int
    tasks: tuple[TaskStatusReadDTO, ...]


@dataclass(frozen=True, slots=True)
class CandidateProfileReadDTO:
    """Zero-PII read projection returned by CandidateProfileReadModelPort."""

    profile_id: UUID
    tenant_id: TenantId
    anonymized_token: UUID
    verified_experience_months: int
    seniority_level: str
    skill_ids: tuple[str, ...]
    project_summaries: tuple[str, ...]
