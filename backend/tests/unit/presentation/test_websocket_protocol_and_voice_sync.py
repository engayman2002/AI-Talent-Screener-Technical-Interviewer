"""Unit tests for ADR-007 Adaptive End-of-Turn Silence & Barge-In Stream Controller."""

from __future__ import annotations

from talent_screener.domain.contexts.live_interview import DifficultyLevel
from talent_screener.presentation.websocket import (
    BargeInStreamController,
    compute_adaptive_silence_threshold_ms,
    is_incomplete_syntax,
)


def test_adaptive_silence_threshold_equation_across_difficulty_and_syntax() -> None:
    # Complete syntax baseline at L1 = 850ms; L5 = 850 + 500 = 1350ms
    complete = "We use monotonic fencing tokens on PostgreSQL leases."
    assert not is_incomplete_syntax(complete)
    assert (
        compute_adaptive_silence_threshold_ms(
            difficulty=DifficultyLevel.L1, trailing_transcript=complete
        )
        == 850
    )
    assert (
        compute_adaptive_silence_threshold_ms(
            difficulty=DifficultyLevel.L3, trailing_transcript=complete
        )
        == 1100
    )
    assert (
        compute_adaptive_silence_threshold_ms(
            difficulty=DifficultyLevel.L5, trailing_transcript=complete
        )
        == 1350
    )

    # Incomplete clause / dangling conjunction / unclosed code block -> +650ms alpha term
    for incomplete in (
        "We route traffic using quorum reads because",
        "Wait, let me sketch the circuit breaker,",
        "```python\ndef handle_turn():",
        "The tradeoff between consistency and (p99",
    ):
        assert is_incomplete_syntax(incomplete)
        assert (
            compute_adaptive_silence_threshold_ms(
                difficulty=DifficultyLevel.L1, trailing_transcript=incomplete
            )
            == 1500
        )
        assert (
            compute_adaptive_silence_threshold_ms(
                difficulty=DifficultyLevel.L5, trailing_transcript=incomplete
            )
            == 2000
        )


def test_barge_in_stream_controller_aborts_remaining_tts_chunks_immediately() -> None:
    ctrl = BargeInStreamController(vad_energy_threshold=0.10)
    chunks = ctrl.begin_stream(
        turn_number=2,
        audio_bytes=b"0123456789" * 20,  # 200 bytes -> 4 chunks of 50 bytes
        chunk_size_bytes=50,
        chunk_duration_ms=40,
    )
    assert len(chunks) == 4
    assert ctrl.is_streaming

    first = ctrl.pull_next_chunk()
    assert first is not None and first.chunk_index == 0
    assert ctrl.delivered_chunk_count == 1
    assert ctrl.remaining_chunk_count == 3

    # Trigger VAD energy below threshold -> does NOT interrupt
    assert ctrl.process_vad_energy(rms_energy=0.04) is None
    assert ctrl.is_streaming

    # VAD speech onset above threshold -> immediately preempts remaining 3 chunks
    receipt = ctrl.process_vad_energy(rms_energy=0.22, elapsed_playback_ms=42)
    assert receipt is not None
    assert receipt.turn_number == 2
    assert receipt.interrupted_at_ms == 42
    assert receipt.delivered_chunks == 1
    assert receipt.cancelled_remaining_chunks == 3
    assert ctrl.is_interrupted
    assert not ctrl.is_streaming
    assert ctrl.pull_next_chunk() is None
    assert ctrl.pull_remaining_chunks() == ()
