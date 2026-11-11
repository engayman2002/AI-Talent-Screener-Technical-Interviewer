"""ISP-segregated Outbound Ports for Live Interview Speech, Guardrails, and FinOps Budgets."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.live_interview.dtos import (
    AudioTranscriptDTO,
    GuardrailEvaluationDTO,
)
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class AudioSttPort(Protocol):
    """Port abstracting low-latency Speech-to-Text transcription."""

    async def transcribe_audio(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        audio_bytes: bytes,
        sample_rate_hz: int = 16_000,
    ) -> AudioTranscriptDTO: ...


@runtime_checkable
class AudioTtsPort(Protocol):
    """Port abstracting streaming Text-to-Speech voice synthesis."""

    async def synthesize_speech(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        text: str,
        voice_id: str = "interviewer_default",
    ) -> bytes: ...


@runtime_checkable
class PromptGuardrailPort(Protocol):
    """Adversarial prompt-injection and rubric-jailbreak firewall port."""

    async def evaluate_candidate_input(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        turn_number: int,
        raw_utterance: str,
    ) -> GuardrailEvaluationDTO: ...


@runtime_checkable
class TokenBudgetEnforcerPort(Protocol):
    """FinOps token ceiling guard for per-session and per-tenant LLM usage."""

    async def ensure_within_budget(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        requested_tokens: int,
        session_limit: int,
    ) -> None: ...

    async def record_consumption(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        tokens_used: int,
    ) -> int: ...
