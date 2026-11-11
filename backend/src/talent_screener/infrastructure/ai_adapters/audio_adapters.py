"""Streaming Audio STT & TTS Infrastructure Adapters with Signal & Fault Validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.common.exceptions import ExternalAdapterFailureError
from talent_screener.application.live_interview.dtos import AudioTranscriptDTO
from talent_screener.domain.shared.value_objects import TenantId


@dataclass(frozen=True, slots=True)
class AudioSignalStats:
    """Computed acoustic envelope metadata extracted prior to STT transcription."""

    byte_length: int
    sample_rate_hz: int
    estimated_duration_ms: int
    rms_energy: float
    zero_byte_ratio: float


@dataclass(frozen=True, slots=True)
class RawTranscriptionResult:
    """Structured speech driver output before conversion into AudioTranscriptDTO."""

    text: str
    duration_ms: int
    speech_cadence_wpm: float
    pause_entropy: float


@runtime_checkable
class SttDriverProtocol(Protocol):
    """Injectable Speech-to-Text engine contract."""

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        sample_rate_hz: int,
        stats: AudioSignalStats,
    ) -> RawTranscriptionResult: ...


@runtime_checkable
class TtsDriverProtocol(Protocol):
    """Injectable Text-to-Speech synthesis engine contract."""

    async def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
    ) -> bytes: ...


class DeterministicSttDriver:
    """Offline STT driver supporting UTF-8 ``TEXT:`` test streams and PCM byte buffers."""

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        sample_rate_hz: int,
        stats: AudioSignalStats,
    ) -> RawTranscriptionResult:
        decoded = audio_bytes.decode("utf-8", errors="ignore").strip()
        if decoded.startswith("TEXT:"):
            transcript = decoded.removeprefix("TEXT:").strip()
        else:
            transcript = (
                f"Spoken engineering explanation captured across {stats.estimated_duration_ms}ms "
                f"at {sample_rate_hz}Hz covering latency tradeoffs and state guarantees."
            )

        words = max(1, len(transcript.split()))
        # Anchor speaking duration realistically (~140 WPM baseline when TEXT: stub is short)
        duration_ms = max(stats.estimated_duration_ms, words * 420, 1200)
        cadence_wpm = round(min(600.0, (words / duration_ms) * 60_000.0), 2)
        pause_entropy = round(min(1.0, max(0.15, 0.35 + (stats.rms_energy * 0.25))), 4)
        return RawTranscriptionResult(
            text=transcript,
            duration_ms=duration_ms,
            speech_cadence_wpm=cadence_wpm,
            pause_entropy=pause_entropy,
        )


class DeterministicTtsDriver:
    """Offline TTS driver synthesizing tagged deterministic PCM-like audio payloads."""

    async def synthesize(self, *, text: str, voice_id: str) -> bytes:
        normalized = " ".join(text.split()).strip()
        header = f"PCM16|VOICE={voice_id}|LEN={len(normalized)}|".encode()
        return header + normalized.encode("utf-8")


class StreamingAudioSttAdapter:
    """Validates audio framing, detects silent/corrupt buffers, and delegates to SttDriver."""

    def __init__(
        self,
        *,
        driver: SttDriverProtocol | None = None,
        min_rms_energy: float = 0.01,
    ) -> None:
        self._driver: SttDriverProtocol = (
            driver if driver is not None else DeterministicSttDriver()
        )
        self._min_rms_energy = min_rms_energy

    async def transcribe_audio(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        audio_bytes: bytes,
        sample_rate_hz: int = 16_000,
    ) -> AudioTranscriptDTO:
        """Validate PCM framing and return structured AudioTranscriptDTO."""
        _ = (tenant_id, session_id)
        if not (8_000 <= sample_rate_hz <= 48_000):
            raise ExternalAdapterFailureError(
                f"Unsupported sample_rate_hz={sample_rate_hz}; expected 8000..48000.",
                code="INVALID_AUDIO_SAMPLE_RATE",
                retryable=False,
            )
        if not audio_bytes:
            raise ExternalAdapterFailureError(
                "Audio payload is empty; cannot transcribe.",
                code="EMPTY_AUDIO_PAYLOAD",
                retryable=False,
            )

        stats = self._compute_signal_stats(audio_bytes, sample_rate_hz=sample_rate_hz)
        if stats.zero_byte_ratio >= 0.99 or stats.rms_energy < self._min_rms_energy:
            raise ExternalAdapterFailureError(
                "Silent or flat-line audio buffer rejected prior to STT.",
                code="AUDIO_SILENCE_REJECTED",
                retryable=False,
            )

        result = await self._driver.transcribe(
            audio_bytes=audio_bytes,
            sample_rate_hz=sample_rate_hz,
            stats=stats,
        )
        cleaned_text = result.text.strip()
        if not cleaned_text:
            raise ExternalAdapterFailureError(
                "STT driver returned empty transcript for non-silent audio.",
                code="EMPTY_TRANSCRIPT_RETURNED",
                retryable=True,
            )

        return AudioTranscriptDTO(
            transcript_text=cleaned_text,
            duration_ms=max(1, result.duration_ms),
            speech_cadence_wpm=min(600.0, max(0.0, result.speech_cadence_wpm)),
            pause_entropy=min(1.0, max(0.0, result.pause_entropy)),
        )

    @staticmethod
    def _compute_signal_stats(audio_bytes: bytes, *, sample_rate_hz: int) -> AudioSignalStats:
        byte_len = len(audio_bytes)
        zero_count = audio_bytes.count(0)
        zero_ratio = zero_count / byte_len
        # Normalize byte deviations around unsigned PCM midpoint (or treat ASCII payloads cleanly)
        is_text_framed = audio_bytes.startswith(b"TEXT:")
        if is_text_framed:
            rms = 0.50
        else:
            mean_sq = sum(((b / 255.0) ** 2) for b in audio_bytes) / byte_len
            rms = math.sqrt(mean_sq)
        bytes_per_ms = max(1.0, (sample_rate_hz * 2) / 1000.0)
        estimated_ms = max(100, int(byte_len / bytes_per_ms))
        return AudioSignalStats(
            byte_length=byte_len,
            sample_rate_hz=sample_rate_hz,
            estimated_duration_ms=estimated_ms,
            rms_energy=rms,
            zero_byte_ratio=zero_ratio,
        )


class StreamingAudioTtsAdapter:
    """Validates prompt text and voice selection before invoking TtsDriverProtocol."""

    def __init__(self, *, driver: TtsDriverProtocol | None = None) -> None:
        self._driver: TtsDriverProtocol = (
            driver if driver is not None else DeterministicTtsDriver()
        )

    async def synthesize_speech(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        text: str,
        voice_id: str = "interviewer_default",
    ) -> bytes:
        """Synthesize interviewer prompt audio stream and reject invalid empty text."""
        _ = (tenant_id, session_id)
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            raise ExternalAdapterFailureError(
                "Cannot synthesize empty interviewer text.",
                code="EMPTY_TTS_TEXT",
                retryable=False,
            )
        if not voice_id.strip():
            raise ExternalAdapterFailureError(
                "voice_id must be non-empty.",
                code="INVALID_VOICE_ID",
                retryable=False,
            )
        audio = await self._driver.synthesize(text=cleaned, voice_id=voice_id.strip())
        if not audio:
            raise ExternalAdapterFailureError(
                "TTS driver returned empty audio buffer.",
                code="EMPTY_TTS_AUDIO",
                retryable=True,
            )
        return audio
