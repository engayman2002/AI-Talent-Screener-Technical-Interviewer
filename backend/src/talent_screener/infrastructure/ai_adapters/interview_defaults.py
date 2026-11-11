"""Minimal in-memory Phase 4 Live Interview adapter defaults prior to Phase 4 LangGraph."""

from __future__ import annotations

from uuid import UUID

from talent_screener.application.common.exceptions import QuotaExceededError
from talent_screener.application.live_interview.dtos import (
    AudioTranscriptDTO,
    CognitiveGraphContextDTO,
    CognitiveGraphResultDTO,
    GuardrailEvaluationDTO,
    InterviewInitCommandDTO,
    InterviewInitOutcomeDTO,
)
from talent_screener.domain.contexts.live_interview import (
    DifficultyLevel,
    PromptHash,
    RoutingAction,
    TurnEvaluationScore,
)
from talent_screener.domain.shared.value_objects import TenantId


class StubAdaptiveInterviewGraphAdapter:
    """Deterministic placeholder AdaptiveInterviewGraphPort until Phase 4 LangGraph."""

    async def initialize_session_graph(
        self, command: InterviewInitCommandDTO
    ) -> InterviewInitOutcomeDTO:
        skill = command.competency_targets[0].canonical_skill_id
        q = f"Describe a production architecture where you applied {skill}."
        return InterviewInitOutcomeDTO(
            opening_question=q,
            opening_prompt_hash=PromptHash.from_prompt_text(q),
            initial_competency_id=command.competency_targets[0].competency_id,
            initial_difficulty=DifficultyLevel.L3,
            checkpoint_version="ckpt-0",
        )

    async def execute_cognitive_turn(
        self, context: CognitiveGraphContextDTO
    ) -> CognitiveGraphResultDTO:
        q = "How did your implementation handle backpressure and failure recovery?"
        return CognitiveGraphResultDTO(
            turn_evaluation=TurnEvaluationScore(4.2, 4.1, 4.0, 4.2, 0.05),
            synthetic_likelihood_score=0.05,
            routing_action_taken=RoutingAction.ESCALATE_DIFFICULTY,
            next_difficulty=DifficultyLevel.L4,
            next_interviewer_utterance=q,
            next_prompt_hash=PromptHash.from_prompt_text(q),
            updated_checkpoint_version=f"ckpt-{context.turn_sequence_number}",
            tokens_used=350,
        )

    async def finalize_session_graph(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        checkpoint_version: str,
    ) -> str:
        return f"Completed interview synthesis for session {session_id}."


class StubAudioSttAdapter:
    """Placeholder AudioSttPort until Phase 4/5 streaming STT adapter."""

    async def transcribe_audio(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        audio_bytes: bytes,
        sample_rate_hz: int = 16_000,
    ) -> AudioTranscriptDTO:
        return AudioTranscriptDTO(
            transcript_text=audio_bytes.decode("utf-8", errors="ignore") or "Transcribed turn.",
            duration_ms=2500,
            speech_cadence_wpm=130.0,
            pause_entropy=0.60,
        )


class StubAudioTtsAdapter:
    """Placeholder AudioTtsPort until Phase 4/5 streaming TTS adapter."""

    async def synthesize_speech(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        text: str,
        voice_id: str = "interviewer_default",
    ) -> bytes:
        return text.encode("utf-8")


class StubPromptGuardrailAdapter:
    """Default PromptGuardrailPort checking basic injection markers."""

    async def evaluate_candidate_input(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        turn_number: int,
        raw_utterance: str,
    ) -> GuardrailEvaluationDTO:
        lower = raw_utterance.lower()
        blocked = "ignore previous instructions" in lower or "system override" in lower
        return GuardrailEvaluationDTO(
            risk_score=0.95 if blocked else 0.02,
            sanitized_text="[BLOCKED]" if blocked else raw_utterance,
            is_suspicious=blocked,
            is_blocked=blocked,
            reason_code="PROMPT_INJECTION_BLOCKED" if blocked else None,
        )


class InMemoryTokenBudgetEnforcerAdapter:
    """Thread-safe in-memory FinOps TokenBudgetEnforcerPort."""

    def __init__(self) -> None:
        self._usage_by_session: dict[tuple[UUID, UUID], int] = {}

    async def ensure_within_budget(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        requested_tokens: int,
        session_limit: int,
    ) -> None:
        current = self._usage_by_session.get((tenant_id.value, session_id), 0)
        if current + requested_tokens > session_limit:
            raise QuotaExceededError(
                f"Session token ceiling {session_limit} exceeded.",
                code="TOKEN_BUDGET_EXCEEDED",
            )

    async def record_consumption(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        tokens_used: int,
    ) -> int:
        key = (tenant_id.value, session_id)
        updated = self._usage_by_session.get(key, 0) + tokens_used
        self._usage_by_session[key] = updated
        return updated
