"""Evaluation & Analytics Bounded Context Domain Events."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from talent_screener.domain.shared.base import DomainEvent


@dataclass(frozen=True, slots=True)
class EvaluationReportGeneratedEvent(DomainEvent):
    """Emitted when an EvaluationReport is finalized and cryptographically hashed."""

    report_id: UUID
    interview_session_id: UUID
    candidate_profile_id: UUID
    requisition_id: UUID
    technical_score: float
    authenticity_score: float
    recommendation_band: str
    report_hash: str


@dataclass(frozen=True, slots=True)
class HighRiskIntegrityAlertRaisedEvent(DomainEvent):
    """Emitted when finalized report has AuthenticityIndex < 0.60 or HIGH/CRITICAL flags."""

    report_id: UUID
    interview_session_id: UUID
    authenticity_score: float
    flag_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecruiterDecisionRecordedEvent(DomainEvent):
    """Emitted when a human recruiter appends an audited disposition override."""

    report_id: UUID
    actor_id: UUID
    previous_band: str
    new_band: str
    justification: str
