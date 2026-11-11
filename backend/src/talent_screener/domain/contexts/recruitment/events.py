"""Recruitment Management Bounded Context Domain Events."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from talent_screener.domain.shared.base import DomainEvent


@dataclass(frozen=True, slots=True)
class JobRequisitionPublishedEvent(DomainEvent):
    """Emitted when a JobRequisition transitions to PUBLISHED."""

    requisition_id: UUID
    title: str
    seniority_level: str
    rubric_version: int
    rubric_version_id: UUID


@dataclass(frozen=True, slots=True)
class JobRequisitionRubricUpdatedEvent(DomainEvent):
    """Emitted when a JobRequisition's competency rubric weights or criteria are updated."""

    requisition_id: UUID
    rubric_version: int
    rubric_version_id: UUID
    competency_count: int


@dataclass(frozen=True, slots=True)
class JobRequisitionArchivedEvent(DomainEvent):
    """Emitted when a JobRequisition is archived."""

    requisition_id: UUID
    previous_status: str
