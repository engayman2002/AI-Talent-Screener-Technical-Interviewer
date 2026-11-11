"""Semantic Matching Engine Domain Events."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from talent_screener.domain.shared.base import DomainEvent


@dataclass(frozen=True, slots=True)
class CandidateMatchedToJobEvent(DomainEvent):
    """Emitted whenever a CandidateProfile is deterministically scored against a rubric."""

    match_result_id: UUID
    requisition_id: UUID
    candidate_profile_id: UUID
    composite_score: float
    assigned_tier: str


@dataclass(frozen=True, slots=True)
class CandidateShortlistedForInterviewEvent(DomainEvent):
    """Emitted when a candidate qualifies for FAST_TRACK or INTERVIEW tier."""

    match_result_id: UUID
    requisition_id: UUID
    candidate_profile_id: UUID
    composite_score: float
    tier: str
    skill_gap_count: int


@dataclass(frozen=True, slots=True)
class CandidateRejectedByGatekeeperEvent(DomainEvent):
    """Emitted when a candidate fails a strict Hard-Skill Gatekeeper (G_hard = 0.0)."""

    match_result_id: UUID
    requisition_id: UUID
    candidate_profile_id: UUID
    failed_gatekeeper_skills: tuple[str, ...]
