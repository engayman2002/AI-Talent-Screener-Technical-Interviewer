"""Outbound DTOs and Use Case Request/Response DTOs for Live Interview Context."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from talent_screener.application.live_interview.dtos.contracts import (
    ClientTelemetryPayloadDTO,
)
from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    RoutingAction,
    SessionStatus,
)
from talent_screener.domain.shared.value_objects import TenantId


@dataclass(frozen=True, slots=True)
class AudioTranscriptDTO:
    """Speech-to-text transcription outcome returned by AudioSttPort."""

    transcript_text: str
    duration_ms: int
    speech_cadence_wpm: float
    pause_entropy: float


@dataclass(frozen=True, slots=True)
class GuardrailEvaluationDTO:
    """Adversarial injection risk assessment returned by PromptGuardrailPort."""

    risk_score: float
    sanitized_text: str
    is_suspicious: bool
    is_blocked: bool
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class InitializeInterviewSessionRequestDTO:
    """Command DTO to provision and start an adaptive InterviewSession."""

    tenant_id: TenantId
    requisition_id: UUID
    candidate_profile_id: UUID
    max_turns: int = 16
    token_budget_limit: int = 45_000
    initial_difficulty: DifficultyLevel = DifficultyLevel.L3


@dataclass(frozen=True, slots=True)
class InitializeInterviewSessionResponseDTO:
    """Response DTO containing session metadata and generated opening question."""

    session_id: UUID
    status: SessionStatus
    active_competency_id: UUID
    initial_difficulty: DifficultyLevel
    opening_question: str
    checkpoint_version: str


@dataclass(frozen=True, slots=True)
class ProcessCandidateTurnRequestDTO:
    """Command DTO carrying a candidate turn utterance/audio and telemetry."""

    tenant_id: TenantId
    session_id: UUID
    turn_number: int
    checkpoint_version: str
    candidate_utterance: str
    remaining_seconds: int
    telemetry: ClientTelemetryPayloadDTO
    correlation_id: str
    audio_bytes: bytes | None = None
    synthesize_voice: bool = False


@dataclass(frozen=True, slots=True)
class ProcessCandidateTurnResponseDTO:
    """Response DTO after evaluating a candidate turn and routing next step."""

    session_id: UUID
    turn_number: int
    session_status: SessionStatus
    routing_action: RoutingAction
    next_difficulty: DifficultyLevel
    next_question: str
    updated_checkpoint_version: str
    effective_quality_score: float
    tokens_consumed: int
    is_completed: bool
    synthesized_audio: bytes = b""


@dataclass(frozen=True, slots=True)
class RecordClientTelemetryRequestDTO:
    """Command DTO to record real-time client integrity telemetry signals."""

    tenant_id: TenantId
    session_id: UUID
    turn_number: int
    focus_loss_count: int = 0
    focus_loss_duration_ms: int = 0
    external_paste_detected: bool = False
    paste_char_count: int = 0
    keystroke_anomaly_detected: bool = False
    ttft_ms: int = 0
    total_duration_ms: int = 0
    speech_cadence_wpm: float = 0.0
    pause_entropy: float = 0.0


@dataclass(frozen=True, slots=True)
class RecordClientTelemetryResponseDTO:
    """Response DTO reporting recorded telemetry snapshot and triggered flags."""

    session_id: UUID
    turn_number: int
    flags_recorded: int
    integrity_flag_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TerminateInterviewEarlyRequestDTO:
    """Command DTO to terminate an active or evaluating InterviewSession early."""

    tenant_id: TenantId
    session_id: UUID
    reason: str


@dataclass(frozen=True, slots=True)
class TerminateInterviewEarlyResponseDTO:
    """Response DTO after terminating an InterviewSession early."""

    session_id: UUID
    status: SessionStatus
    reason: str
    total_turns: int


@dataclass(frozen=True, slots=True)
class CompleteInterviewSessionRequestDTO:
    """Command DTO to finalize and complete an InterviewSession."""

    tenant_id: TenantId
    session_id: UUID
    checkpoint_version: str


@dataclass(frozen=True, slots=True)
class CompleteInterviewSessionResponseDTO:
    """Response DTO confirming completion of an InterviewSession."""

    session_id: UUID
    status: SessionStatus
    total_turns: int
    total_tokens_consumed: int
    synthesis_summary: str
