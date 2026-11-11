"""Live Interview Bounded Context Domain Events."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from talent_screener.domain.shared.base import DomainEvent


@dataclass(frozen=True, slots=True)
class InterviewSessionStartedEvent(DomainEvent):
    """Emitted when an InterviewSession transitions into IN_PROGRESS."""

    session_id: UUID
    requisition_id: UUID
    candidate_profile_id: UUID
    initial_competency_id: UUID


@dataclass(frozen=True, slots=True)
class AdaptiveTurnCompletedEvent(DomainEvent):
    """Emitted upon recording an evaluated AdaptiveTurn."""

    session_id: UUID
    turn_number: int
    competency_id: UUID
    difficulty_level: int
    effective_quality_score: float
    tokens_used: int


@dataclass(frozen=True, slots=True)
class SocraticProbeTriggeredEvent(DomainEvent):
    """Emitted when a Socratic follow-up probe is triggered for a competency."""

    session_id: UUID
    turn_number: int
    competency_id: UUID
    probe_index: int
    strategy: str


@dataclass(frozen=True, slots=True)
class IntegrityAnomalyDetectedEvent(DomainEvent):
    """Emitted when one or more IntegrityFlag records are raised on a turn."""

    session_id: UUID
    turn_number: int
    flag_codes: tuple[str, ...]
    max_severity: str


@dataclass(frozen=True, slots=True)
class InterviewSessionCompletedEvent(DomainEvent):
    """Emitted when an InterviewSession finishes final evaluation cleanly."""

    session_id: UUID
    total_turns: int
    total_tokens_consumed: int


@dataclass(frozen=True, slots=True)
class InterviewSessionTerminatedEarlyEvent(DomainEvent):
    """Emitted when an InterviewSession is terminated early or abandoned."""

    session_id: UUID
    terminal_status: str
    reason: str
    total_turns: int
