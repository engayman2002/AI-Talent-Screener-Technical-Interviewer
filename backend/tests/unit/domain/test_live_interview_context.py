"""Unit tests for the Live Interview Bounded Context."""

from __future__ import annotations

from uuid import UUID

import pytest

from talent_screener.domain.contexts.live_interview import (
    AdaptiveRoutingDomainService,
    AdaptiveTurn,
    AdaptiveTurnCompletedEvent,
    DifficultyLevel,
    IntegrityAnomalyDetectedEvent,
    IntegrityFlag,
    InterviewSession,
    InterviewSessionCompletedEvent,
    InterviewSessionStartedEvent,
    InterviewSessionTerminatedEarlyEvent,
    InvalidTurnOrderError,
    PromptHash,
    ResponseLatencyMetric,
    RoutingAction,
    SessionStatus,
    SocraticProbe,
    SocraticProbeLimitExceededError,
    SocraticProbeTriggeredEvent,
    TelemetrySnapshot,
    TokenBudgetExceededError,
    TurnEvaluationScore,
    TurnLimitExceededError,
)
from talent_screener.domain.shared import (
    ConfidenceScore,
    InvalidStateTransitionError,
    InvariantViolationError,
    TenantId,
    VersionNumber,
    generate_uuid7,
)


def _turn(
    turn_number: int,
    competency_id: UUID,
    *,
    accuracy: float = 4.2,
    depth: float = 4.0,
    tradeoff: float = 4.0,
    specificity: float = 4.0,
    tokens: int = 500,
    probe: SocraticProbe | None = None,
    flags: tuple[IntegrityFlag, ...] = (),
) -> AdaptiveTurn:
    prompt = "Explain lease fencing in distributed lock coordination."
    return AdaptiveTurn(
        turn_number=turn_number,
        competency_id=competency_id,
        difficulty=DifficultyLevel.L3,
        question_prompt=prompt,
        prompt_hash=PromptHash.from_prompt_text(prompt),
        candidate_response="We enforce monotonic fencing tokens validated atomically at storage.",
        evaluation=TurnEvaluationScore(
            technical_accuracy=accuracy,
            depth_of_understanding=depth,
            tradeoff_awareness=tradeoff,
            contextual_specificity=specificity,
        ),
        tokens_used=tokens,
        socratic_probe=probe,
        latency_metric=ResponseLatencyMetric(ttft_ms=1800, total_duration_ms=12000),
        integrity_flags=flags,
    )


@pytest.mark.unit
def test_difficulty_level_and_turn_evaluation_score_math() -> None:
    assert DifficultyLevel.L3.escalate() is DifficultyLevel.L4
    assert DifficultyLevel.L5.escalate() is DifficultyLevel.L5
    assert DifficultyLevel.L1.deescalate() is DifficultyLevel.L1

    with pytest.raises(InvariantViolationError):
        DifficultyLevel.from_int(6)

    # Strong turn: Q_raw = (0.35*4.0 + 0.30*4.0 + 0.20*4.0 + 0.15*4.0) / 5 = 0.80
    strong = TurnEvaluationScore(4.0, 4.0, 4.0, 4.0)
    assert strong.raw_quality_score() == pytest.approx(0.80)
    assert strong.effective_quality_score() == pytest.approx(0.80)

    # Fundamental inaccuracy penalty when accuracy < 1.5
    flawed = TurnEvaluationScore(0.75, 4.0, 4.0, 4.0)
    q_raw = flawed.raw_quality_score()
    assert flawed.effective_quality_score() == pytest.approx(q_raw * (0.75 / 1.5) ** 2)


def _session_status(session: InterviewSession) -> SessionStatus:
    return session.status


@pytest.mark.unit
def test_interview_session_state_machine_probe_cap_ordering_and_token_budget() -> None:
    tid = TenantId.generate()
    comp1 = generate_uuid7()
    comp2 = generate_uuid7()
    session = InterviewSession(
        tenant_id=tid,
        requisition_id=generate_uuid7(),
        candidate_profile_id=generate_uuid7(),
        rubric_version=VersionNumber(1),
        competency_queue=[comp1, comp2],
        max_turns=6,
        token_budget_limit=2000,
    )
    assert _session_status(session) is SessionStatus.SCHEDULED

    # Cannot record turn before IN_PROGRESS
    with pytest.raises(InvalidStateTransitionError):
        session.record_turn(_turn(1, comp1))

    # Illegal direct transition SCHEDULED -> IN_PROGRESS rejected
    with pytest.raises(InvalidStateTransitionError):
        session.start()

    session.initialize()
    assert _session_status(session) is SessionStatus.INITIALIZING
    session.start()
    assert _session_status(session) is SessionStatus.IN_PROGRESS
    assert session.active_competency_id == comp1
    start_events = session.pull_domain_events()
    assert len(start_events) == 1
    assert isinstance(start_events[0], InterviewSessionStartedEvent)

    # Out-of-order turn rejected
    with pytest.raises(InvalidTurnOrderError):
        session.record_turn(_turn(2, comp1))

    # Turn 1 with IntegrityFlag + SocraticProbe #1
    probe1 = SocraticProbe(
        competency_id=comp1,
        probe_index=1,
        strategy="UNSTATED_TRADEOFF_PROBE",
        target_concept="Clock drift impact on leases",
        probe_prompt="What happens if NTP drifts backward during a GC pause?",
        prompt_hash=PromptHash.from_prompt_text("probe1"),
    )
    flag = IntegrityFlag(
        flag_code="CLIPBOARD_PASTE_BURST",
        severity="HIGH",
        turn_number=1,
        confidence=ConfidenceScore(0.88),
        evidence_summary="Large block pasted in 120ms.",
    )
    session.record_turn(
        _turn(1, comp1, tokens=600, probe=probe1, flags=(flag,)),
        telemetry=TelemetrySnapshot(
            turn_number=1, external_paste_detected=True, paste_char_count=220
        ),
    )
    assert session.probe_count_for(comp1) == 1

    # Turn 2 with SocraticProbe #2 (allowed: N_probe <= 2)
    probe2 = SocraticProbe(
        competency_id=comp1,
        probe_index=2,
        strategy="AUTHENTICITY_VERIFICATION_PROBE",
        target_concept="Split-brain fencing",
        probe_prompt="How does storage reject stale epoch writers?",
        prompt_hash=PromptHash.from_prompt_text("probe2"),
    )
    session.record_turn(_turn(2, comp1, tokens=600, probe=probe2), next_competency_id=comp2)
    assert session.probe_count_for(comp1) == 2
    assert session.active_competency_id == comp2

    # Turn 3 trying a 3rd SocraticProbe on comp1 fails INV-LI-01
    with pytest.raises(SocraticProbeLimitExceededError) as exc_probe:
        probe3_bad = SocraticProbe(
            competency_id=comp1,
            probe_index=2,
            strategy="CLARIFY_VAGUE_CLAIM",
            target_concept="Extra probe",
            probe_prompt="One more?",
            prompt_hash=PromptHash.from_prompt_text("probe3"),
        )
        session.record_turn(_turn(3, comp1, tokens=200, probe=probe3_bad))
    assert exc_probe.value.code == "SOCRATIC_PROBE_LIMIT_EXCEEDED"

    # Turn 3 exceeding token budget fails
    with pytest.raises(TokenBudgetExceededError):
        session.record_turn(_turn(3, comp2, tokens=900))

    session.record_turn(_turn(3, comp2, tokens=500))
    events = session.pull_domain_events()
    assert any(isinstance(e, SocraticProbeTriggeredEvent) for e in events)
    assert any(isinstance(e, IntegrityAnomalyDetectedEvent) for e in events)
    assert any(isinstance(e, AdaptiveTurnCompletedEvent) for e in events)

    session.begin_final_evaluation()
    session.complete()
    assert _session_status(session) is SessionStatus.COMPLETED
    comp_events = session.pull_domain_events()
    assert len(comp_events) == 1
    assert isinstance(comp_events[0], InterviewSessionCompletedEvent)


@pytest.mark.unit
def test_interview_session_early_termination_and_max_turns() -> None:
    tid = TenantId.generate()
    comp = generate_uuid7()
    session = InterviewSession(
        tenant_id=tid,
        requisition_id=generate_uuid7(),
        candidate_profile_id=generate_uuid7(),
        rubric_version=VersionNumber(1),
        competency_queue=[comp],
        max_turns=1,
    )
    session.initialize()
    session.start()
    session.record_turn(_turn(1, comp, tokens=100))
    with pytest.raises(TurnLimitExceededError):
        session.record_turn(_turn(2, comp, tokens=100))

    session.terminate_early("Security injection threshold exceeded")
    assert _session_status(session) is SessionStatus.TERMINATED_EARLY
    ev = session.pull_domain_events()[-1]
    assert isinstance(ev, InterviewSessionTerminatedEarlyEvent)

    # Abandon transition from SCHEDULED
    abandoned = InterviewSession(
        tenant_id=tid,
        requisition_id=generate_uuid7(),
        candidate_profile_id=generate_uuid7(),
        rubric_version=VersionNumber(1),
        competency_queue=[comp],
    )
    abandoned.abandon("Candidate never joined")
    assert _session_status(abandoned) is SessionStatus.ABANDONED


@pytest.mark.unit
def test_adaptive_routing_domain_service_mathematics() -> None:
    svc = AdaptiveRoutingDomainService()

    # D_dem and M_k calculations
    d1 = svc.compute_demonstrated_depth(
        difficulty=DifficultyLevel.L3, effective_quality=0.80, probe_index=0
    )
    assert d1 == pytest.approx(2.40)
    d2 = svc.compute_demonstrated_depth(
        difficulty=DifficultyLevel.L4, effective_quality=0.85, probe_index=1
    )
    assert d2 == pytest.approx(4.0 * 0.85 * (1.0 - 0.08))
    mastery = svc.compute_competency_mastery(
        [(DifficultyLevel.L3, 0.80, 0), (DifficultyLevel.L4, 0.85, 1)]
    )
    expected_consistency = (3 * d1 + 4 * d2) / 7.0
    assert mastery == pytest.approx(0.70 * max(d1, d2) + 0.30 * expected_consistency)

    # Difficulty calibration
    assert (
        svc.calibrate_next_difficulty(DifficultyLevel.L3, TurnEvaluationScore(4.5, 4.2, 4.0, 4.0))
        is DifficultyLevel.L4
    )
    assert (
        svc.calibrate_next_difficulty(DifficultyLevel.L3, TurnEvaluationScore(3.0, 3.0, 3.0, 3.0))
        is DifficultyLevel.L3
    )
    assert (
        svc.calibrate_next_difficulty(DifficultyLevel.L3, TurnEvaluationScore(1.5, 1.5, 1.5, 1.5))
        is DifficultyLevel.L2
    )

    # P1: Budget exhausted -> CONCLUDE_INTERVIEW
    p1 = svc.decide_next_route(
        evaluation=TurnEvaluationScore(4.5, 4.5, 4.5, 4.5),
        current_difficulty=DifficultyLevel.L3,
        current_mastery=2.0,
        target_mastery=4.0,
        active_probe_count=0,
        primary_questions_asked=1,
        completed_turns=16,
        max_turns=16,
        elapsed_seconds=500.0,
        max_wall_clock_seconds=1800.0,
        tokens_consumed=5000,
        token_budget_limit=45000,
        remaining_competencies_count=2,
    )
    assert p1.action is RoutingAction.CONCLUDE_INTERVIEW

    # P2: Suspicion -> SOCRATIC_AUTHENTICITY_PROBE
    p2 = svc.decide_next_route(
        evaluation=TurnEvaluationScore(4.5, 4.5, 4.5, 4.5),
        current_difficulty=DifficultyLevel.L3,
        current_mastery=2.0,
        target_mastery=4.0,
        active_probe_count=0,
        primary_questions_asked=1,
        completed_turns=3,
        max_turns=16,
        elapsed_seconds=200.0,
        max_wall_clock_seconds=1800.0,
        tokens_consumed=3000,
        token_budget_limit=45000,
        remaining_competencies_count=2,
        turn_suspicion_risk=0.68,
        rolling_authenticity_index=0.80,
    )
    assert p2.action is RoutingAction.SOCRATIC_AUTHENTICITY_PROBE

    # P3: Shallow response -> SOCRATIC_DEPTH_PROBE
    p3 = svc.decide_next_route(
        evaluation=TurnEvaluationScore(3.0, 2.5, 2.5, 2.0),
        current_difficulty=DifficultyLevel.L3,
        current_mastery=2.0,
        target_mastery=4.0,
        active_probe_count=1,
        primary_questions_asked=1,
        completed_turns=3,
        max_turns=16,
        elapsed_seconds=200.0,
        max_wall_clock_seconds=1800.0,
        tokens_consumed=3000,
        token_budget_limit=45000,
        remaining_competencies_count=2,
    )
    assert p3.action is RoutingAction.SOCRATIC_DEPTH_PROBE

    # P4: Strong answer below target mastery -> ESCALATE_DIFFICULTY
    p4 = svc.decide_next_route(
        evaluation=TurnEvaluationScore(4.5, 4.2, 4.0, 4.0),
        current_difficulty=DifficultyLevel.L3,
        current_mastery=2.5,
        target_mastery=4.0,
        active_probe_count=0,
        primary_questions_asked=1,
        completed_turns=3,
        max_turns=16,
        elapsed_seconds=200.0,
        max_wall_clock_seconds=1800.0,
        tokens_consumed=3000,
        token_budget_limit=45000,
        remaining_competencies_count=2,
    )
    assert p4.action is RoutingAction.ESCALATE_DIFFICULTY
    assert p4.next_difficulty is DifficultyLevel.L4

    # P5: Probe cap reached (2) with remaining competencies -> TRANSITION_NEXT_COMPETENCY
    p5 = svc.decide_next_route(
        evaluation=TurnEvaluationScore(2.5, 2.5, 2.5, 2.0),
        current_difficulty=DifficultyLevel.L3,
        current_mastery=2.5,
        target_mastery=4.0,
        active_probe_count=2,
        primary_questions_asked=1,
        completed_turns=5,
        max_turns=16,
        elapsed_seconds=300.0,
        max_wall_clock_seconds=1800.0,
        tokens_consumed=4000,
        token_budget_limit=45000,
        remaining_competencies_count=1,
    )
    assert p5.action is RoutingAction.TRANSITION_NEXT_COMPETENCY

    # P6: Queue empty and competency finished -> CONCLUDE_INTERVIEW
    p6 = svc.decide_next_route(
        evaluation=TurnEvaluationScore(4.5, 4.5, 4.5, 4.5),
        current_difficulty=DifficultyLevel.L4,
        current_mastery=4.2,
        target_mastery=4.0,
        active_probe_count=0,
        primary_questions_asked=1,
        completed_turns=8,
        max_turns=16,
        elapsed_seconds=600.0,
        max_wall_clock_seconds=1800.0,
        tokens_consumed=6000,
        token_budget_limit=45000,
        remaining_competencies_count=0,
    )
    assert p6.action is RoutingAction.CONCLUDE_INTERVIEW
