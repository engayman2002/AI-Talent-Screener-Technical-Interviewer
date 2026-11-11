"""Unit tests for ADR-005 4-Plane Graph State, Idempotent Reducers, and ADR-009 Checkpointer."""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest

from talent_screener.application.common.exceptions import (
    ResourceConflictError,
    ResourceNotFoundError,
)
from talent_screener.application.live_interview.dtos import (
    ClientTelemetryPayloadDTO,
    CognitiveGraphContextDTO,
    CompetencyBriefingTargetDTO,
    InterviewInitCommandDTO,
)
from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    IntegrityFlag,
    PromptHash,
    RoutingAction,
    TurnEvaluationScore,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import (
    ConfidenceScore,
    TenantId,
    VersionNumber,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview import (
    AdaptiveRoutingPlane,
    ClientTelemetryRecord,
    EvaluatedTurnRecord,
    InMemoryFencedStateCheckpointer,
    InterviewGraphPhase,
    LangGraphAdaptiveInterviewAdapter,
    PromptHashAuditRecord,
    SocraticProbeState,
    StateReducer,
    TurnTranscriptRecord,
    build_initial_graph_state,
)


def _sample_competencies() -> tuple[CompetencyBriefingTargetDTO, ...]:
    return (
        CompetencyBriefingTargetDTO(
            competency_id=generate_uuid7(),
            canonical_skill_id="distributed-systems",
            weight=0.60,
            target_depth=4,
            is_gatekeeper=True,
            ideal_anchor_descriptor="Consensus, quorum replication, and failover bounds",
            probe_priority=0.90,
        ),
        CompetencyBriefingTargetDTO(
            competency_id=generate_uuid7(),
            canonical_skill_id="redis-caching",
            weight=0.40,
            target_depth=3,
            is_gatekeeper=False,
            ideal_anchor_descriptor="Eviction policies, thundering-herd mitigation, and TTLs",
            probe_priority=0.65,
        ),
    )


def _sample_telemetry() -> ClientTelemetryPayloadDTO:
    return ClientTelemetryPayloadDTO(
        focus_loss_count=0,
        focus_loss_duration_ms=0,
        external_paste_detected=False,
        paste_char_count=0,
        keystroke_anomaly_detected=False,
        response_latency_ms=1400,
        speech_cadence_wpm=132.0,
        pause_entropy=0.45,
    )


def test_four_plane_state_and_idempotent_reducers() -> None:
    tenant = TenantId.generate()
    comps = _sample_competencies()
    opening_hash = PromptHash.from_prompt_text("Explain quorum reads versus linearizable reads.")
    state = build_initial_graph_state(
        session_id=generate_uuid7(),
        tenant_id=tenant,
        requisition_id=generate_uuid7(),
        candidate_profile_id=generate_uuid7(),
        rubric_version=VersionNumber(1),
        competency_targets=comps,
        candidate_project_highlights=("Built multi-region KV store at 50k QPS",),
        opening_question="Explain quorum reads versus linearizable reads.",
        opening_prompt_hash=opening_hash,
        opening_tokens_used=120,
    )

    assert state.routing.phase is InterviewGraphPhase.QUESTION_GENERATED
    assert state.routing.active_competency_id == comps[0].competency_id
    assert state.rubric.remaining_count_after(comps[0].competency_id) == 1
    assert state.rubric.next_competency_after(comps[1].competency_id) is None
    assert state.has_prompt_hash(opening_hash)

    now: datetime = utc_now()
    # Test bounded sliding window eviction + idempotent replay on duplicate turn_number
    current_state = state
    for turn_idx in range(1, 5):
        next_prompt = f"Question variation {turn_idx}?"
        next_hash = PromptHash.from_prompt_text(next_prompt)
        turn = TurnTranscriptRecord(
            turn_number=turn_idx,
            competency_id=comps[0].competency_id,
            question_prompt=current_state.routing.active_prompt_text,
            candidate_response=f"Response {turn_idx} using raft quorum.",
            difficulty=DifficultyLevel.L3,
            is_socratic_probe=False,
            probe_index=0,
            recorded_at=now,
        )
        evaluation = EvaluatedTurnRecord(
            turn_number=turn_idx,
            competency_id=comps[0].competency_id,
            difficulty=DifficultyLevel.L3,
            evaluation=TurnEvaluationScore(4.2, 4.0, 3.8, 4.1),
            weighted_turn_score=0.80,
            synthetic_likelihood_score=0.08,
            probe_index=0,
            rationale="Solid architectural reasoning.",
        )
        tel_rec = ClientTelemetryRecord(
            turn_number=turn_idx,
            payload=_sample_telemetry(),
            p_client=0.0,
            p_cadence=0.0,
            p_stylo=0.05,
            turn_suspicion_risk=0.05,
        )
        probe = SocraticProbeState(
            turn_number=turn_idx,
            competency_id=comps[0].competency_id,
            probe_index=1,
            strategy="UNSTATED_TRADEOFF_PROBE",
            target_concept="distributed-systems",
            probe_prompt=f"Probe for turn {turn_idx}?",
            prompt_hash=PromptHash.from_prompt_text(f"Probe for turn {turn_idx}?"),
        )
        flag = IntegrityFlag(
            flag_code="LOW_SAMPLE_FLAG",
            severity="LOW",
            turn_number=turn_idx,
            confidence=ConfidenceScore(0.4),
            evidence_summary="Minor pause variance.",
        )
        next_routing = AdaptiveRoutingPlane(
            phase=InterviewGraphPhase.QUESTION_GENERATED,
            active_competency_id=comps[0].competency_id,
            current_difficulty=DifficultyLevel.L3,
            questions_asked_in_active_competency=turn_idx,
            probes_used_in_active_competency=0,
            mastery_by_competency=current_state.routing.mastery_by_competency,
            rolling_authenticity_index=1.0,
            total_tokens_consumed=120 + turn_idx * 150,
            completed_turns=turn_idx,
            active_prompt_text=next_prompt,
            active_prompt_hash=next_hash,
            last_routing_action=RoutingAction.ESCALATE_DIFFICULTY,
        )
        current_state = StateReducer.with_recorded_turn(
            current_state,
            turn=turn,
            evaluation=evaluation,
            telemetry=tel_rec,
            next_routing=next_routing,
            new_fencing_token=turn_idx + 1,
            probe=probe,
            next_prompt_audit=PromptHashAuditRecord(
                turn_number=turn_idx,
                role="ESCALATED_QUESTION",
                prompt_hash=next_hash,
            ),
            flags=(flag,),
            window_size=2,
        )

    assert len(current_state.ledger.turns) == 4
    assert len(current_state.ledger.recent_dialogue_window) == 2
    assert [t.turn_number for t in current_state.ledger.recent_dialogue_window] == [3, 4]

    # Idempotent replay on turn 4 must NOT duplicate turn/evaluation/probe/flag counts
    deduped_turns, _ = StateReducer.append_turn(
        current_state.ledger.turns, current_state.ledger.turns[-1], window_size=2
    )
    assert len(deduped_turns) == 4
    assert len(current_state.observations_for(comps[0].competency_id)) == 4


def test_checkpointer_fencing_tokens_tenant_isolation_and_restart_resume() -> None:
    tenant_a = TenantId.generate()
    tenant_b = TenantId.generate()
    comps = _sample_competencies()
    checkpointer = InMemoryFencedStateCheckpointer()
    adapter_a = LangGraphAdaptiveInterviewAdapter(checkpointer=checkpointer)
    session_id = generate_uuid7()

    async def _run() -> None:
        init_outcome = await adapter_a.initialize_session_graph(
            InterviewInitCommandDTO(
                session_id=session_id,
                tenant_id=tenant_a,
                requisition_id=generate_uuid7(),
                candidate_profile_id=generate_uuid7(),
                rubric_version=VersionNumber(1),
                competency_targets=comps,
                candidate_project_highlights=("Designed Raft metadata store at 40k QPS",),
            )
        )
        assert checkpointer.load_latest(tenant_b, session_id) is None
        assert (
            checkpointer.load_version(tenant_b, session_id, init_outcome.checkpoint_version)
            is None
        )

        # Missing session or stale checkpoint fails cleanly
        with pytest.raises(ResourceNotFoundError, match="GRAPH_CHECKPOINT_NOT_FOUND"):
            await adapter_a.finalize_session_graph(
                tenant_id=tenant_a,
                session_id=uuid4(),
                checkpoint_version=init_outcome.checkpoint_version,
            )

        with pytest.raises(ResourceConflictError, match="CHECKPOINT_VERSION_CONFLICT"):
            await adapter_a.execute_cognitive_turn(
                CognitiveGraphContextDTO(
                    session_id=session_id,
                    tenant_id=tenant_a,
                    checkpoint_version="wrong-ckpt",
                    turn_sequence_number=1,
                    candidate_project_highlights=("Designed Raft metadata store",),
                    active_competency=comps[0],
                    current_difficulty=DifficultyLevel.L3,
                    socratic_followup_count=0,
                    primary_questions_asked=0,
                    max_probes_allowed=2,
                    remaining_seconds=1700,
                    sanitized_candidate_utterance="We use quorum reads with lease timers.",
                    turn_telemetry_signals=_sample_telemetry(),
                )
            )

        # Turn 1 succeeds and commits checkpoint #2 (fencing_token=2)
        turn1_res = await adapter_a.execute_cognitive_turn(
            CognitiveGraphContextDTO(
                session_id=session_id,
                tenant_id=tenant_a,
                checkpoint_version=init_outcome.checkpoint_version,
                turn_sequence_number=1,
                candidate_project_highlights=("Designed Raft metadata store",),
                active_competency=comps[0],
                current_difficulty=DifficultyLevel.L3,
                socratic_followup_count=0,
                primary_questions_asked=0,
                max_probes_allowed=2,
                remaining_seconds=1650,
                sanitized_candidate_utterance=(
                    "In our Raft store at 40k QPS we enforce leader leases with fencing "
                    "tokens and write-ahead log quorum replication; the tradeoff is +4ms p99 "
                    "write latency versus preventing split-brain stale reads during failover."
                ),
                turn_telemetry_signals=_sample_telemetry(),
            )
        )

        # Direct stale fencing token write to checkpointer raises STALE_FENCING_TOKEN
        initial_snap = checkpointer.load_version(
            tenant_a, session_id, init_outcome.checkpoint_version
        )
        assert initial_snap is not None
        with pytest.raises(ResourceConflictError, match="STALE_FENCING_TOKEN"):
            checkpointer.save_checkpoint(initial_snap.state)

        # Restart simulation: brand new adapter backed by the same checkpointer resumes Turn 2
        restarted_adapter = LangGraphAdaptiveInterviewAdapter(checkpointer=checkpointer)
        turn2_res = await restarted_adapter.execute_cognitive_turn(
            CognitiveGraphContextDTO(
                session_id=session_id,
                tenant_id=tenant_a,
                checkpoint_version=turn1_res.updated_checkpoint_version,
                turn_sequence_number=2,
                candidate_project_highlights=("Designed Raft metadata store",),
                active_competency=comps[0],
                current_difficulty=turn1_res.next_difficulty,
                socratic_followup_count=0,
                primary_questions_asked=1,
                max_probes_allowed=2,
                remaining_seconds=1580,
                sanitized_candidate_utterance=(
                    "For recovery bounds we snapshot WAL segments every 250ms and apply "
                    "backoff with jitter to prevent thundering-herd retries on follower timeouts."
                ),
                turn_telemetry_signals=_sample_telemetry(),
            )
        )
        summary = await restarted_adapter.finalize_session_graph(
            tenant_id=tenant_a,
            session_id=session_id,
            checkpoint_version=turn2_res.updated_checkpoint_version,
        )
        assert "Verified 2 turn(s)" in summary
        assert len(checkpointer.list_history(tenant_a, session_id)) == 4

    asyncio.run(_run())
