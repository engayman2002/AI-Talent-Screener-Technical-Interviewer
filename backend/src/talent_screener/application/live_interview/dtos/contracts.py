"""Conceptual DTO contracts for AdaptiveInterviewGraphPort (Phase 1 Section 5.2 & Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    PromptHash,
    RoutingAction,
    TurnEvaluationScore,
)
from talent_screener.domain.shared.value_objects import TenantId, VersionNumber


@dataclass(frozen=True, slots=True)
class CompetencyBriefingTargetDTO:
    """Frozen competency rubric slice injected into interview graph initialization."""

    competency_id: UUID
    canonical_skill_id: str
    weight: float
    target_depth: int
    is_gatekeeper: bool
    ideal_anchor_descriptor: str
    probe_priority: float


@dataclass(frozen=True, slots=True)
class ClientTelemetryPayloadDTO:
    """Client-side behavioral and cadence telemetry bundled with a candidate turn."""

    focus_loss_count: int
    focus_loss_duration_ms: int
    external_paste_detected: bool
    paste_char_count: int
    keystroke_anomaly_detected: bool
    response_latency_ms: int
    speech_cadence_wpm: float
    pause_entropy: float


@dataclass(frozen=True, slots=True)
class InterviewInitCommandDTO:
    """Command initializing the stateful adaptive interview graph for a session."""

    session_id: UUID
    tenant_id: TenantId
    requisition_id: UUID
    candidate_profile_id: UUID
    rubric_version: VersionNumber
    competency_targets: tuple[CompetencyBriefingTargetDTO, ...]
    candidate_project_highlights: tuple[str, ...]
    max_probes_per_competency: int = 2
    max_wall_clock_seconds: int = 1800


@dataclass(frozen=True, slots=True)
class InterviewInitOutcomeDTO:
    """Initial question and checkpoint version emitted upon graph initialization."""

    opening_question: str
    opening_prompt_hash: PromptHash
    initial_competency_id: UUID
    initial_difficulty: DifficultyLevel
    checkpoint_version: str


@dataclass(frozen=True, slots=True)
class CognitiveGraphContextDTO:
    """Input state snapshot crossing AdaptiveInterviewGraphPort on each turn."""

    session_id: UUID
    tenant_id: TenantId
    checkpoint_version: str
    turn_sequence_number: int
    candidate_project_highlights: tuple[str, ...]
    active_competency: CompetencyBriefingTargetDTO
    current_difficulty: DifficultyLevel
    socratic_followup_count: int
    primary_questions_asked: int
    max_probes_allowed: int
    remaining_seconds: int
    sanitized_candidate_utterance: str
    turn_telemetry_signals: ClientTelemetryPayloadDTO


@dataclass(frozen=True, slots=True)
class CognitiveGraphResultDTO:
    """Validated cognitive evaluation and routing outcome returned across ACL."""

    turn_evaluation: TurnEvaluationScore
    synthetic_likelihood_score: float
    routing_action_taken: RoutingAction
    next_difficulty: DifficultyLevel
    next_interviewer_utterance: str
    next_prompt_hash: PromptHash
    updated_checkpoint_version: str
    tokens_used: int
