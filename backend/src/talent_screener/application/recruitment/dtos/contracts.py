"""Immutable Request, Response, and Read DTOs for Recruitment Management Use Cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from talent_screener.domain.contexts.recruitment.value_objects import (
    RequisitionStatus,
    SeniorityLevel,
)
from talent_screener.domain.shared.value_objects import TenantId


@dataclass(frozen=True, slots=True)
class CompetencyCriterionInputDTO:
    """Immutable specification for creating or updating a rubric competency criterion."""

    name: str
    canonical_skill_id: str
    weight: float
    target_depth: int
    is_gatekeeper: bool = False
    raw_importance: float = 1.0
    ideal_anchor_descriptor: str = ""
    min_applied_months: int = 0
    strict_disqualification: bool = True


@dataclass(frozen=True, slots=True)
class CompetencyCriterionReadDTO:
    """Immutable read projection of a CompetencyCriterion entity."""

    criterion_id: UUID
    name: str
    canonical_skill_id: str
    weight: float
    target_depth: int
    is_gatekeeper: bool
    ideal_anchor_descriptor: str


@dataclass(frozen=True, slots=True)
class TierThresholdsDTO:
    """Immutable cutoff threshold specification for candidate screening tiers."""

    fast_track_threshold: float = 88.0
    interview_threshold: float = 75.0
    review_threshold: float = 58.0


@dataclass(frozen=True, slots=True)
class CreateJobRequisitionRequestDTO:
    """Command DTO to create a draft JobRequisition with weighted rubric criteria."""

    tenant_id: TenantId
    title: str
    description: str
    seniority_level: SeniorityLevel
    criteria: tuple[CompetencyCriterionInputDTO, ...]
    tier_thresholds: TierThresholdsDTO | None = None
    interview_timebox_minutes: int = 30


@dataclass(frozen=True, slots=True)
class CreateJobRequisitionResponseDTO:
    """Outcome DTO emitted after creating a draft JobRequisition."""

    requisition_id: UUID
    tenant_id: TenantId
    status: RequisitionStatus
    rubric_version: int
    rubric_version_id: UUID


@dataclass(frozen=True, slots=True)
class PublishJobRequisitionRequestDTO:
    """Command DTO to publish a JobRequisition."""

    tenant_id: TenantId
    requisition_id: UUID


@dataclass(frozen=True, slots=True)
class PublishJobRequisitionResponseDTO:
    """Outcome DTO emitted after publishing a JobRequisition."""

    requisition_id: UUID
    status: RequisitionStatus
    rubric_version: int
    rubric_version_id: UUID


@dataclass(frozen=True, slots=True)
class UpdateJobRubricRequestDTO:
    """Command DTO to update competency criteria and thresholds on an unlocked requisition."""

    tenant_id: TenantId
    requisition_id: UUID
    criteria: tuple[CompetencyCriterionInputDTO, ...]
    tier_thresholds: TierThresholdsDTO | None = None


@dataclass(frozen=True, slots=True)
class UpdateJobRubricResponseDTO:
    """Outcome DTO emitted after updating a JobRequisition rubric."""

    requisition_id: UUID
    rubric_version: int
    rubric_version_id: UUID
    competency_count: int


@dataclass(frozen=True, slots=True)
class LockJobRequisitionRubricRequestDTO:
    """Command DTO to lock a JobRequisition rubric on active batch or interview creation."""

    tenant_id: TenantId
    requisition_id: UUID
    lock_source_type: Literal["SCREENING_BATCH", "INTERVIEW_SESSION"]
    lock_source_id: UUID


@dataclass(frozen=True, slots=True)
class LockJobRequisitionRubricResponseDTO:
    """Outcome DTO confirming rubric lock status."""

    requisition_id: UUID
    is_rubric_locked: bool
    active_batch_count: int
    active_session_count: int


@dataclass(frozen=True, slots=True)
class ArchiveJobRequisitionRequestDTO:
    """Command DTO to archive a JobRequisition."""

    tenant_id: TenantId
    requisition_id: UUID


@dataclass(frozen=True, slots=True)
class ArchiveJobRequisitionResponseDTO:
    """Outcome DTO emitted after archiving a JobRequisition."""

    requisition_id: UUID
    status: RequisitionStatus


@dataclass(frozen=True, slots=True)
class GetJobRequisitionQueryDTO:
    """Query DTO to retrieve a JobRequisition read model."""

    tenant_id: TenantId
    requisition_id: UUID


@dataclass(frozen=True, slots=True)
class JobRequisitionReadDto:
    """Complete immutable read projection of a JobRequisition aggregate."""

    requisition_id: UUID
    tenant_id: TenantId
    title: str
    description: str
    seniority_level: SeniorityLevel
    status: RequisitionStatus
    rubric_version: int
    rubric_version_id: UUID
    is_rubric_locked: bool
    interview_timebox_minutes: int
    tier_thresholds: TierThresholdsDTO
    criteria: tuple[CompetencyCriterionReadDTO, ...]
    gatekeeper_skills: frozenset[str]
    aggregate_version: int
