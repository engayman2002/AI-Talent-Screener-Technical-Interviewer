"""Unit tests for the Recruitment Management Bounded Context."""

from __future__ import annotations

import pytest

from talent_screener.domain.contexts.recruitment import (
    CompetencyCriterion,
    CompetencyWeightVector,
    GatekeeperRequiredError,
    GatekeeperRule,
    HardSkillGatekeeperSet,
    JobRequisition,
    JobRequisitionArchivedEvent,
    JobRequisitionPublishedEvent,
    JobRequisitionRubricUpdatedEvent,
    RequisitionStatus,
    RubricLockedError,
    RubricWeightSumError,
    SeniorityLevel,
    TierThresholdConfig,
)
from talent_screener.domain.shared import (
    InvalidStateTransitionError,
    InvariantViolationError,
    TenantId,
    VersionNumber,
    generate_uuid7,
)


def _sample_criteria() -> list[CompetencyCriterion]:
    return [
        CompetencyCriterion(
            name="Distributed Systems",
            canonical_skill_id="SKILL_DISTRIBUTED_SYSTEMS",
            weight=0.5,
            target_depth=4,
            is_gatekeeper=True,
            raw_importance=3.0,
        ),
        CompetencyCriterion(
            name="Python Backend",
            canonical_skill_id="SKILL_LANG_PYTHON",
            weight=0.3,
            target_depth=4,
            is_gatekeeper=False,
            raw_importance=2.0,
        ),
        CompetencyCriterion(
            name="Observability",
            canonical_skill_id="SKILL_OPENTELEMETRY",
            weight=0.2,
            target_depth=3,
            is_gatekeeper=False,
            raw_importance=1.0,
        ),
    ]


@pytest.mark.unit
def test_competency_weight_vector_tolerance_and_normalization() -> None:
    # Within 1e-6 tolerance succeeds
    vec = CompetencyWeightVector((("a", 0.5000004), ("b", 0.5000004)))
    assert vec.weight_for("a") == pytest.approx(0.5000004)

    # Exceeding 1e-6 tolerance raises RubricWeightSumError
    with pytest.raises(RubricWeightSumError) as exc:
        CompetencyWeightVector((("a", 0.5), ("b", 0.49)))
    assert exc.value.code == "RUBRIC_WEIGHT_SUM_INVALID"

    # Zero or negative weights rejected
    with pytest.raises(InvariantViolationError):
        CompetencyWeightVector((("a", 0.0), ("b", 1.0)))

    # Dynamic normalization from arbitrary positive raw scores
    normalized = CompetencyWeightVector.normalize_from_raw({"x": 3.0, "y": 1.0})
    assert normalized.weight_for("x") == pytest.approx(0.75)
    assert normalized.weight_for("y") == pytest.approx(0.25)


@pytest.mark.unit
def test_hard_skill_gatekeeper_set_and_tier_thresholds() -> None:
    gk = HardSkillGatekeeperSet.from_skill_ids(["SKILL_PYTHON", "SKILL_SQL"], min_applied_months=24)
    assert len(gk.skill_ids) == 2

    with pytest.raises(InvariantViolationError) as exc_limit:
        HardSkillGatekeeperSet(
            frozenset(GatekeeperRule(f"SKILL_{idx}") for idx in range(6))
        )
    assert exc_limit.value.code == "GATEKEEPER_LIMIT_EXCEEDED"

    cfg = TierThresholdConfig(
        fast_track_threshold=90.0,
        interview_threshold=75.0,
        review_threshold=60.0,
    )
    assert cfg.fast_track_threshold > cfg.interview_threshold > cfg.review_threshold

    with pytest.raises(InvariantViolationError) as exc_thresh:
        TierThresholdConfig(
            fast_track_threshold=75.0,
            interview_threshold=75.0,
            review_threshold=60.0,
        )
    assert exc_thresh.value.code == "INVALID_TIER_THRESHOLDS"


def _read_status(req: JobRequisition) -> RequisitionStatus:
    return req.status


@pytest.mark.unit
def test_job_requisition_lifecycle_events_and_gatekeeper_invariant() -> None:
    tid = TenantId.generate()
    req = JobRequisition(
        tenant_id=tid,
        title="Staff Distributed Systems Engineer",
        description="Architect low-latency distributed screening workflows.",
        seniority_level=SeniorityLevel.STAFF,
        criteria=_sample_criteria(),
    )
    assert _read_status(req) is RequisitionStatus.DRAFT
    assert req.seniority_level.baseline_depth == 4

    req.publish()
    assert _read_status(req) is RequisitionStatus.PUBLISHED
    events = req.pull_domain_events()
    assert len(events) == 1
    assert isinstance(events[0], JobRequisitionPublishedEvent)

    req.pause()
    assert _read_status(req) is RequisitionStatus.PAUSED
    req.publish()
    assert _read_status(req) is RequisitionStatus.PUBLISHED
    req.pull_domain_events()

    req.archive()
    assert _read_status(req) is RequisitionStatus.ARCHIVED
    arch_events = req.pull_domain_events()
    assert len(arch_events) == 1
    assert isinstance(arch_events[0], JobRequisitionArchivedEvent)

    with pytest.raises(InvalidStateTransitionError):
        req.publish()

    # Publishing without any gatekeeper fails INV-RM-01
    no_gk = JobRequisition(
        tenant_id=tid,
        title="Backend Python Engineer",
        description="Develop clean APIs and asynchronous batch pipelines.",
        seniority_level=SeniorityLevel.MID,
        criteria=[
            CompetencyCriterion(
                name="Python",
                canonical_skill_id="SKILL_PYTHON",
                weight=1.0,
                target_depth=3,
                is_gatekeeper=False,
            )
        ],
    )
    with pytest.raises(GatekeeperRequiredError):
        no_gk.publish()


@pytest.mark.unit
def test_job_requisition_rubric_locking_and_dynamic_weight_recalculation() -> None:
    tid = TenantId.generate()
    req = JobRequisition(
        tenant_id=tid,
        title="Principal AI Platform Engineer",
        description="Lead multi-agent LangGraph interview platform architecture.",
        seniority_level=SeniorityLevel.PRINCIPAL,
        criteria=_sample_criteria(),
    )
    assert req.rubric_version == VersionNumber(1)
    assert not req.is_rubric_locked

    # Dynamic weight recalculation when unlocked
    rebalanced = req.rebalance_weights_dynamically(gatekeeper_multiplier=2.0)
    # raw importances: gatekeeper(3*2)=6, py=2, obs=1 -> total=9
    assert rebalanced.weight_for(str(req.criteria[0].id)) == pytest.approx(6.0 / 9.0)
    assert req.rubric_version == VersionNumber(2)
    events = req.pull_domain_events()
    assert len(events) == 1
    assert isinstance(events[0], JobRequisitionRubricUpdatedEvent)

    # Lock via active ScreeningBatch
    batch_id = generate_uuid7()
    req.register_active_screening_batch(batch_id)
    assert req.is_rubric_locked
    with pytest.raises(RubricLockedError):
        req.update_rubric(criteria=_sample_criteria())

    # Unlock after batch completes
    req.release_screening_batch(batch_id)
    assert not req.is_rubric_locked
    req.update_rubric(criteria=_sample_criteria())
    assert req.rubric_version == VersionNumber(3)

    # Lock permanently once an InterviewSession exists or completes
    session_id = generate_uuid7()
    req.register_active_interview_session(session_id)
    assert req.is_rubric_locked
    req.complete_interview_session(session_id)
    assert req.is_rubric_locked
    with pytest.raises(RubricLockedError):
        req.rebalance_weights_dynamically()
