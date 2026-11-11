"""Document Intelligence Bounded Context Domain Events."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from talent_screener.domain.shared.base import DomainEvent


@dataclass(frozen=True, slots=True)
class ScreeningBatchCreatedEvent(DomainEvent):
    """Emitted when a ScreeningBatch aggregate is created and validated."""

    batch_id: UUID
    requisition_id: UUID
    total_files: int


@dataclass(frozen=True, slots=True)
class DocumentExtractionSucceededEvent(DomainEvent):
    """Emitted when a CV inside a ScreeningBatch finishes structured extraction."""

    batch_id: UUID
    task_id: UUID
    candidate_profile_id: UUID
    content_sha256: str


@dataclass(frozen=True, slots=True)
class DocumentExtractionFailedEvent(DomainEvent):
    """Emitted when document extraction fails or is quarantined."""

    batch_id: UUID
    task_id: UUID
    error_code: str
    quarantined: bool


@dataclass(frozen=True, slots=True)
class CandidateProfileNormalizedEvent(DomainEvent):
    """Emitted when a zero-PII CandidateProfile is constructed and verified."""

    candidate_profile_id: UUID
    anonymized_token: UUID
    verified_experience_months: int
    skill_count: int


@dataclass(frozen=True, slots=True)
class ScreeningBatchCompletedEvent(DomainEvent):
    """Emitted when all tasks in a ScreeningBatch reach terminal state."""

    batch_id: UUID
    final_status: str
    completed_count: int
    failed_count: int
