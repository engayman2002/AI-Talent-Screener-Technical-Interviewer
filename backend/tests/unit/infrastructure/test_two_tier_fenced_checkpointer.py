"""Unit tests for ADR-009 Two-Tier (L1 + L2 SQL) Fenced LangGraph Checkpointer."""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from talent_screener.application.common.exceptions import (
    ExternalAdapterFailureError,
    ResourceConflictError,
    UnauthorizedTenantAccessError,
)
from talent_screener.application.live_interview.dtos import (
    ClientTelemetryPayloadDTO,
    CompetencyBriefingTargetDTO,
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
    InterviewGraphPhase,
    InterviewGraphState,
    InterviewStateCheckpointerProtocol,
    PromptHashAuditRecord,
    SocraticProbeState,
    StateReducer,
    TurnTranscriptRecord,
    build_initial_graph_state,
)
from talent_screener.infrastructure.persistence.checkpointing import (
    TwoTierFencedStateCheckpointer,
)
from talent_screener.infrastructure.persistence.relational import (
    DatabaseConnectionConfig,
    SqlDatabaseManager,
)


def _initial_state(tenant_id: TenantId) -> InterviewGraphState:
    comp_id = generate_uuid7()
    target = CompetencyBriefingTargetDTO(
        competency_id=comp_id,
        canonical_skill_id="SKILL_SYSTEMS",
        weight=1.0,
        target_depth=4,
        is_gatekeeper=True,
        ideal_anchor_descriptor="Explain Paxos/Raft log replication",
        probe_priority=0.9,
    )
    return build_initial_graph_state(
        session_id=generate_uuid7(),
        tenant_id=tenant_id,
        requisition_id=generate_uuid7(),
        candidate_profile_id=generate_uuid7(),
        rubric_version=VersionNumber(2),
        competency_targets=(target,),
        candidate_project_highlights=("Built Raft KV store",),
        opening_question="Walk me through Raft leader election safety.",
        opening_prompt_hash=PromptHash.from_prompt_text("Walk me through Raft leader election."),
        opening_tokens_used=120,
        fencing_token=1,
    )


def _advance_turn(state: InterviewGraphState, *, fence: int) -> InterviewGraphState:
    comp_id = state.rubric.competency_queue[0].competency_id
    next_turn = state.routing.completed_turns + 1
    q_hash = PromptHash.from_prompt_text(f"Question #{next_turn + 1}")
    next_routing = AdaptiveRoutingPlane(
        phase=InterviewGraphPhase.SOCRATIC_PROBING,
        active_competency_id=comp_id,
        current_difficulty=DifficultyLevel.L4,
        questions_asked_in_active_competency=1,
        probes_used_in_active_competency=1,
        mastery_by_competency=MappingProxyType({comp_id: 0.78}),
        rolling_authenticity_index=0.94,
        total_tokens_consumed=state.routing.total_tokens_consumed + 250,
        completed_turns=next_turn,
        active_prompt_text=f"Follow-up #{next_turn}",
        active_prompt_hash=q_hash,
        last_routing_action=RoutingAction.SOCRATIC_DEPTH_PROBE,
        last_routing_rule_id="R2",
    )
    return StateReducer.with_recorded_turn(
        state,
        turn=TurnTranscriptRecord(
            turn_number=next_turn,
            competency_id=comp_id,
            question_prompt=state.routing.active_prompt_text,
            candidate_response="Leader election compares log term and index completeness.",
            difficulty=DifficultyLevel.L3,
            is_socratic_probe=False,
            probe_index=0,
            recorded_at=utc_now(),
        ),
        evaluation=EvaluatedTurnRecord(
            turn_number=next_turn,
            competency_id=comp_id,
            difficulty=DifficultyLevel.L3,
            evaluation=TurnEvaluationScore(4.2, 4.0, 3.8, 4.1, 0.08),
            weighted_turn_score=0.81,
            synthetic_likelihood_score=0.08,
            probe_index=1,
            rationale="Accurate leader completeness check",
        ),
        telemetry=ClientTelemetryRecord(
            turn_number=next_turn,
            payload=ClientTelemetryPayloadDTO(0, 0, False, 0, False, 1400, 130.0, 0.25),
            p_client=0.0,
            p_cadence=0.05,
            p_stylo=0.05,
            turn_suspicion_risk=0.05,
        ),
        next_routing=next_routing,
        new_fencing_token=fence,
        probe=SocraticProbeState(
            turn_number=next_turn,
            competency_id=comp_id,
            probe_index=1,
            strategy="UNSTATED_TRADEOFF_PROBE",
            target_concept="Split-vote livelock avoidance",
            probe_prompt=f"Follow-up #{next_turn}",
            prompt_hash=q_hash,
        ),
        next_prompt_audit=PromptHashAuditRecord(
            turn_number=next_turn, role="SOCRATIC_PROBE", prompt_hash=q_hash
        ),
        flags=(
            IntegrityFlag(
                flag_code="MINOR_CADENCE_SHIFT",
                severity="LOW",
                turn_number=next_turn,
                confidence=ConfidenceScore(0.65),
                evidence_summary="Slight pause shift",
            ),
        ),
    )


@pytest.mark.unit
def test_two_tier_checkpointer_l1_hit_l2_fallback_and_restart_survival(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "checkpoints.db"
    clock = [100.0]
    db1 = SqlDatabaseManager(DatabaseConnectionConfig(dsn=str(db_file)))
    ckpt1 = TwoTierFencedStateCheckpointer(
        db=db1, l1_ttl_seconds=60.0, clock_fn=lambda: clock[0]
    )
    assert isinstance(ckpt1, InterviewStateCheckpointerProtocol)

    tenant = TenantId.generate()
    state1 = _initial_state(tenant)
    session_id = state1.metadata.session_id

    try:
        snap1 = ckpt1.save_checkpoint(state1)
        assert snap1.step_number == 1 and snap1.fencing_token == 1

        # Warm read hits L1 cache directly without touching L2 SQL
        hit1 = ckpt1.get_checkpoint(tenant, session_id)
        assert hit1 is not None and hit1.checkpoint_version == state1.checkpoint_version
        assert ckpt1.l1_hit_count == 1 and ckpt1.l2_hit_count == 0

        # Advance turn and write step 2 via explicit put_checkpoint
        state2 = _advance_turn(state1, fence=2)
        snap2 = ckpt1.put_checkpoint(state2, expected_previous_version=snap1.checkpoint_version)
        assert snap2.step_number == 2

        # Expire L1 cache by advancing clock -> forces L2 SQL fallback & SHA check
        clock[0] += 120.0
        fallback = ckpt1.load_latest(tenant, session_id)
        assert fallback is not None
        assert ckpt1.l2_hit_count == 1
        assert fallback.state.routing.completed_turns == 1
        assert fallback.state.ledger.turns[0].candidate_response.startswith("Leader election")
        assert fallback.state.ledger.socratic_probes[0].strategy == "UNSTATED_TRADEOFF_PROBE"

        # Immediately re-reading hits newly warmed L1 again
        warmed = ckpt1.load_version(tenant, session_id, state2.checkpoint_version)
        assert warmed is not None and ckpt1.l1_hit_count == 2

        # Simulate full process restart against same SQLite DB
        db1.close()
        db2 = SqlDatabaseManager(DatabaseConnectionConfig(dsn=str(db_file)))
        try:
            ckpt2 = TwoTierFencedStateCheckpointer(db=db2)
            history = ckpt2.list_history(tenant, session_id)
            assert len(history) == 2
            assert [h.fencing_token for h in history] == [1, 2]
            resumed = ckpt2.load_latest(tenant, session_id)
            assert resumed is not None
            assert resumed.state == state2
        finally:
            db2.close()
    finally:
        with contextlib.suppress(Exception):
            db1.close()


@pytest.mark.unit
def test_two_tier_checkpointer_fencing_tamper_detection_and_tenant_boundary() -> None:
    db = SqlDatabaseManager(DatabaseConnectionConfig(dsn=":memory:"))
    ckpt = TwoTierFencedStateCheckpointer(db=db)
    tenant_a = TenantId.generate()
    tenant_b = TenantId.generate()

    try:
        state1 = _initial_state(tenant_a)
        session_id = state1.metadata.session_id
        snap1 = ckpt.save_checkpoint(state1)

        state2 = _advance_turn(state1, fence=5)
        snap2 = ckpt.save_checkpoint(state2, expected_previous_version=snap1.checkpoint_version)

        # Reject stale lower and equal fencing tokens across cache invalidation
        ckpt.invalidate_l1(tenant_a, session_id)
        with pytest.raises(ResourceConflictError) as exc_stale:
            ckpt.save_checkpoint(_advance_turn(state2, fence=5))
        assert exc_stale.value.code == "STALE_FENCING_TOKEN"

        with pytest.raises(ResourceConflictError) as exc_lower:
            ckpt.put_checkpoint(_advance_turn(state2, fence=3))
        assert exc_lower.value.code == "STALE_FENCING_TOKEN"

        # Reject optimistic checkpoint version mismatch
        with pytest.raises(ResourceConflictError) as exc_ver:
            ckpt.save_checkpoint(
                _advance_turn(state2, fence=6),
                expected_previous_version=snap1.checkpoint_version,
            )
        assert exc_ver.value.code == "CHECKPOINT_VERSION_CONFLICT"

        # Cross-tenant boundary enforcement
        assert ckpt.load_latest(tenant_b, session_id) is None
        assert ckpt.list_history(tenant_b, session_id) == ()
        cross_meta = replace(state2.metadata, tenant_id=tenant_b)
        cross_state = replace(state2, metadata=cross_meta, fencing_token=99)
        with pytest.raises(UnauthorizedTenantAccessError, match="CHECKPOINT_TENANT_MISMATCH"):
            ckpt.save_checkpoint(cross_state)

        # Tamper detection on L2 SQL payload triggers CHECKPOINT_CORRUPTION_DETECTED
        db.execute(
            "UPDATE interview_state_checkpoints SET state_json = ? "
            "WHERE tenant_id = ? AND session_id = ? AND checkpoint_version = ?",
            ('{"tampered":true}', str(tenant_a.value), str(session_id), snap2.checkpoint_version),
        )
        ckpt.invalidate_l1(tenant_a, session_id)
        with pytest.raises(ExternalAdapterFailureError) as exc_corrupt:
            ckpt.load_latest(tenant_a, session_id)
        assert exc_corrupt.value.code == "CHECKPOINT_CORRUPTION_DETECTED"
    finally:
        db.close()
