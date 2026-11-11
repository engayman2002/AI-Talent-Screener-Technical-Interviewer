"""ADR-007 Adaptive End-of-Turn Silence Calculator & Preemptive Barge-In Stream Controller."""

from __future__ import annotations

from dataclasses import dataclass

from talent_screener.domain.contexts.live_interview.value_objects import DifficultyLevel

BASE_SILENCE_MS: int = 850
INCOMPLETE_SYNTAX_ALPHA_MS: int = 650
COMPLEXITY_BETA_MS: int = 500

_INCOMPLETE_TRAILING_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "although",
        "as",
        "because",
        "but",
        "for",
        "however",
        "if",
        "into",
        "or",
        "since",
        "so",
        "such",
        "that",
        "the",
        "then",
        "though",
        "to",
        "versus",
        "vs",
        "when",
        "where",
        "whereas",
        "which",
        "while",
        "with",
    }
)

_INCOMPLETE_SUFFIXES: tuple[str, ...] = (
    ",",
    ":",
    ";",
    "(",
    "[",
    "{",
    "=",
    "+",
    "-",
    "/",
    "->",
    "=>",
    "such as",
    "for example",
    "and then",
)


def is_incomplete_syntax(trailing_transcript: str) -> bool:
    """Detect whether trailing utterance text ends mid-clause or inside an unclosed code block."""
    cleaned = trailing_transcript.strip()
    if not cleaned:
        return False

    # Unclosed markdown fenced code block or unbalanced brackets indicates mid-thought pause
    if cleaned.count("```") % 2 == 1:
        return True
    if cleaned.count("(") > cleaned.count(")") or cleaned.count("{") > cleaned.count("}"):
        return True

    lowered = cleaned.lower()
    if any(lowered.endswith(suffix) for suffix in _INCOMPLETE_SUFFIXES):
        return True

    if lowered[-1] in {".", "?", "!"}:
        return False

    tokens = lowered.split()
    if not tokens:
        return False
    last_word = tokens[-1].strip("\"'`")
    return last_word in _INCOMPLETE_TRAILING_WORDS


def compute_adaptive_silence_threshold_ms(
    *,
    difficulty: DifficultyLevel,
    trailing_transcript: str,
) -> int:
    """Compute Phase 5 Eq. T_silence(L_diff, syntax) in milliseconds.

    T_silence = T_base (850ms)
              + alpha_syntax (650ms) * I(incomplete_syntax)
              + beta_complexity (500ms) * ((L_diff - 1) / 4)
    """
    syntax_indicator = 1 if is_incomplete_syntax(trailing_transcript) else 0
    complexity_term = COMPLEXITY_BETA_MS * ((int(difficulty) - 1) / 4.0)
    total = BASE_SILENCE_MS + (INCOMPLETE_SYNTAX_ALPHA_MS * syntax_indicator) + complexity_term
    return round(total)


@dataclass(frozen=True, slots=True)
class TtsAudioChunkFrame:
    """Discrete outbound TTS audio frame queued for WebSocket delivery."""

    turn_number: int
    chunk_index: int
    audio_bytes: bytes
    duration_ms: int
    is_final_chunk: bool


@dataclass(frozen=True, slots=True)
class BargeInInterruptionReceipt:
    """Immutable receipt emitted when candidate barge-in preempts outbound TTS playback."""

    turn_number: int
    interrupted_at_ms: int
    delivered_chunks: int
    cancelled_remaining_chunks: int
    reason: str


class BargeInStreamController:
    """Manages active outbound TTS frames and halts remaining chunks upon candidate barge-in."""

    def __init__(self, *, vad_energy_threshold: float = 0.08) -> None:
        if vad_energy_threshold <= 0.0:
            raise ValueError("vad_energy_threshold must be positive.")
        self._vad_threshold = vad_energy_threshold
        self._active_turn: int = 0
        self._chunks: list[TtsAudioChunkFrame] = []
        self._cursor: int = 0
        self._chunk_duration_ms: int = 40
        self._is_streaming: bool = False
        self._is_interrupted: bool = False
        self._interrupted_at_ms: int | None = None

    @property
    def is_streaming(self) -> bool:
        """True while unconsumed TTS chunks remain active and uninterrupted."""
        return self._is_streaming and not self._is_interrupted and self._cursor < len(self._chunks)

    @property
    def is_interrupted(self) -> bool:
        return self._is_interrupted

    @property
    def interrupted_at_ms(self) -> int | None:
        return self._interrupted_at_ms

    @property
    def delivered_chunk_count(self) -> int:
        return self._cursor

    @property
    def remaining_chunk_count(self) -> int:
        return max(0, len(self._chunks) - self._cursor)

    def begin_stream(
        self,
        *,
        turn_number: int,
        audio_bytes: bytes,
        chunk_size_bytes: int = 64,
        chunk_duration_ms: int = 40,
    ) -> tuple[TtsAudioChunkFrame, ...]:
        """Partition synthesized TTS audio into sequential chunks ready for streaming."""
        if chunk_size_bytes <= 0 or chunk_duration_ms <= 0:
            raise ValueError("chunk_size_bytes and chunk_duration_ms must be positive.")
        raw_slices = [
            audio_bytes[i : i + chunk_size_bytes]
            for i in range(0, len(audio_bytes), chunk_size_bytes)
        ] or [b""]
        total = len(raw_slices)
        frames = [
            TtsAudioChunkFrame(
                turn_number=turn_number,
                chunk_index=idx,
                audio_bytes=chunk,
                duration_ms=chunk_duration_ms,
                is_final_chunk=(idx == total - 1),
            )
            for idx, chunk in enumerate(raw_slices)
        ]
        self._active_turn = turn_number
        self._chunks = frames
        self._cursor = 0
        self._chunk_duration_ms = chunk_duration_ms
        self._is_streaming = True
        self._is_interrupted = False
        self._interrupted_at_ms = None
        return tuple(frames)

    def pull_next_chunk(self) -> TtsAudioChunkFrame | None:
        """Deliver next TTS chunk unless halted by barge-in or stream completion."""
        if not self.is_streaming:
            self._is_streaming = False
            return None
        chunk = self._chunks[self._cursor]
        self._cursor += 1
        if self._cursor >= len(self._chunks):
            self._is_streaming = False
        return chunk

    def pull_remaining_chunks(self) -> tuple[TtsAudioChunkFrame, ...]:
        """Drain all remaining uninterrupted chunks in order."""
        out: list[TtsAudioChunkFrame] = []
        while (chunk := self.pull_next_chunk()) is not None:
            out.append(chunk)
        return tuple(out)

    def trigger_barge_in(
        self,
        *,
        interrupted_at_ms: int | None = None,
        reason: str = "CANDIDATE_BARGE_IN",
    ) -> BargeInInterruptionReceipt:
        """Immediately halt all remaining outbound TTS chunks and capture interrupt offset."""
        delivered = self._cursor
        cancelled = max(0, len(self._chunks) - delivered)
        computed_ms = (
            interrupted_at_ms
            if interrupted_at_ms is not None and interrupted_at_ms >= 0
            else delivered * self._chunk_duration_ms
        )
        self._is_interrupted = True
        self._is_streaming = False
        self._interrupted_at_ms = computed_ms
        self._chunks = self._chunks[:delivered]
        return BargeInInterruptionReceipt(
            turn_number=self._active_turn,
            interrupted_at_ms=computed_ms,
            delivered_chunks=delivered,
            cancelled_remaining_chunks=cancelled,
            reason=reason,
        )

    def process_vad_energy(
        self, *, rms_energy: float, elapsed_playback_ms: int | None = None
    ) -> BargeInInterruptionReceipt | None:
        """Automatically trigger barge-in when inbound RMS exceeds VAD threshold during TTS."""
        if not self.is_streaming or rms_energy < self._vad_threshold:
            return None
        return self.trigger_barge_in(
            interrupted_at_ms=elapsed_playback_ms,
            reason="CANDIDATE_VAD_SPEECH_ONSET",
        )
