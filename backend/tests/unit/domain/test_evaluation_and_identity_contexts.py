"""Unit tests for Evaluation & Analytics and Identity & Governance Bounded Contexts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from talent_screener.domain.contexts.evaluation_analytics import (
    AuthenticityIndex,
    CompetencyScorecardItem,
    CompositeRubricScore,
    CompositeScoreMismatchError,
    ConfidenceInterval,
    EvaluationReport,
    EvaluationReportGeneratedEvent,
    HighRiskIntegrityAlertRaisedEvent,
    HiringRecommendationBand,
    IntegrityAuditEntry,
    MissingEvidenceTurnError,
    RecruiterDecisionRecordedEvent,
    RecruiterOverrideAudit,
    ReportImmutableError,
)
from talent_screener.domain.contexts.identity_governance import (
    CandidateErasureRequestedEvent,
    ConsentRecord,
    ConsentScope,
    ConsentStatus,
    QuotaPolicy,
    RetentionPolicy,
    TenantAccount,
)
from talent_screener.domain.shared import (
    InvalidStateTransitionError,
    InvariantViolationError,
    TenantId,
    generate_uuid7,
)


def _sample_scorecard() -> list[CompetencyScorecardItem]:
    return [
        CompetencyScorecardItem(
            competency_id=generate_uuid7(),
            competency_name="Distributed Systems",
            weight=0.60,
            target_depth=4,
            achieved_depth=4.2,
            normalized_score=85.0,
            evidence_turn_numbers=(1, 2),
            evidence_quotes=("Explained monotonic fencing tokens.",),
            rationale="Strong production depth.",
        ),
        CompetencyScorecardItem(
            competency_id=generate_uuid7(),
            competency_name="Python Async",
            weight=0.40,
            target_depth=4,
            achieved_depth=3.8,
            normalized_score=75.0,
            evidence_turn_numbers=(3,),
            evidence_quotes=("Used bounded asyncio TaskGroups.",),
            rationale="Solid concurrency control.",
        ),
    ]


@pytest.mark.unit
def test_authenticity_index_penalty_and_recovery_formulas() -> None:
    ci = ConfidenceInterval(0.75, 0.90)
    assert ci.width == pytest.approx(0.15)
    with pytest.raises(InvariantViolationError):
        ConfidenceInterval(0.90, 0.70)

    p_client = AuthenticityIndex.compute_client_penalty(
        blur_count=2,
        blur_duration_seconds=5.0,
        external_paste=True,
        keystroke_anomaly=False,
    )
    assert 0.45 < p_client <= 1.0

    r_turn = AuthenticityIndex.compute_turn_suspicion_risk(
        p_client=p_client,
        p_cadence=0.70,
        p_stylo=0.80,
    )
    assert r_turn >= 0.60

    initial = AuthenticityIndex(score=1.0)
    penalized = initial.apply_turn_update(turn_suspicion_risk=0.75, socratic_verified=False)
    assert penalized.score == pytest.approx(1.0 - 0.22 * (0.75 - 0.25))

    recovered = penalized.apply_turn_update(turn_suspicion_risk=0.10, socratic_verified=True)
    assert recovered.score == pytest.approx(penalized.score + 0.08)

    assert AuthenticityIndex(score=0.90).authenticity_moderator() == pytest.approx(1.00)
    assert AuthenticityIndex(score=0.70).authenticity_moderator() == pytest.approx(
        0.85 + 0.75 * 0.10
    )
    assert AuthenticityIndex(score=0.30).authenticity_moderator() == pytest.approx(0.25)


@pytest.mark.unit
def test_evaluation_report_invariants_immutability_and_override_audit() -> None:
    tid = TenantId.generate()
    items = _sample_scorecard()  # weighted tech = 0.6*85 + 0.4*75 = 51 + 30 = 81.0

    # Missing turn evidence rejected (INV-EA-01)
    with pytest.raises(MissingEvidenceTurnError):
        CompetencyScorecardItem(
            competency_id=generate_uuid7(),
            competency_name="SQL",
            weight=1.0,
            target_depth=3,
            achieved_depth=3.0,
            normalized_score=70.0,
            evidence_turn_numbers=(),
        )

    # Low authenticity reports orthogonally without mutating technical score (INV-EA-02)
    low_auth = AuthenticityIndex(score=0.42)
    comp_score = CompositeRubricScore.from_components(
        technical_interview_score=81.0,
        cv_match_score=84.0,
        authenticity_index=low_auth,
    )
    assert comp_score.technical_interview_score == pytest.approx(81.0)
    assert comp_score.recommendation_index < 81.0

    # Mismatched composite score rejected
    mismatched_score = CompositeRubricScore.from_components(
        technical_interview_score=95.0,
        cv_match_score=84.0,
        authenticity_index=low_auth,
    )
    with pytest.raises(CompositeScoreMismatchError):
        EvaluationReport(
            tenant_id=tid,
            interview_session_id=generate_uuid7(),
            requisition_id=generate_uuid7(),
            candidate_profile_id=generate_uuid7(),
            scorecard_items=items,
            composite_score=mismatched_score,
            authenticity_index=low_auth,
            recommendation_band=HiringRecommendationBand.BORDERLINE_HOLD,
            executive_summary="Strong technical depth with integrity warning.",
        )

    # Referencing non-existent turn rejected when valid_turn_numbers supplied
    with pytest.raises(MissingEvidenceTurnError):
        EvaluationReport(
            tenant_id=tid,
            interview_session_id=generate_uuid7(),
            requisition_id=generate_uuid7(),
            candidate_profile_id=generate_uuid7(),
            scorecard_items=items,
            composite_score=comp_score,
            authenticity_index=low_auth,
            recommendation_band=HiringRecommendationBand.BORDERLINE_HOLD,
            executive_summary="Strong technical depth with integrity warning.",
            valid_turn_numbers=frozenset({1, 2}),
        )

    report = EvaluationReport(
        tenant_id=tid,
        interview_session_id=generate_uuid7(),
        requisition_id=generate_uuid7(),
        candidate_profile_id=generate_uuid7(),
        scorecard_items=items,
        composite_score=comp_score,
        authenticity_index=low_auth,
        recommendation_band=HiringRecommendationBand.BORDERLINE_HOLD,
        executive_summary="Strong technical depth flagged for human authenticity review.",
        integrity_entries=[
            IntegrityAuditEntry(
                turn_number=1,
                flag_code="PASTE_ANOMALY",
                severity="HIGH",
                suspicion_risk=0.78,
                description="External clipboard paste burst followed by high perplexity.",
            )
        ],
        valid_turn_numbers=frozenset({1, 2, 3}),
    )

    digest = report.finalize()
    assert report.is_finalized
    assert report.report_hash == digest
    final_events = report.pull_domain_events()
    assert any(isinstance(e, EvaluationReportGeneratedEvent) for e in final_events)
    assert any(isinstance(e, HighRiskIntegrityAlertRaisedEvent) for e in final_events)

    # Finalized report refuses direct mutation or re-finalization (INV-EA-03)
    with pytest.raises(ReportImmutableError):
        report.update_scorecard(scorecard_items=items, composite_score=comp_score)
    with pytest.raises(ReportImmutableError):
        report.finalize()

    # Append-only recruiter override audit is permitted after finalization
    override = RecruiterOverrideAudit(
        actor_id=generate_uuid7(),
        previous_band=HiringRecommendationBand.BORDERLINE_HOLD,
        new_band=HiringRecommendationBand.HIRE,
        justification="Live engineering follow-up confirmed candidate solved problem cleanly.",
    )
    report.record_recruiter_override(override)
    assert report.initial_recommendation_band is HiringRecommendationBand.BORDERLINE_HOLD
    assert report.effective_recommendation_band is HiringRecommendationBand.HIRE
    assert len(report.override_audits) == 1
    override_events = report.pull_domain_events()
    assert len(override_events) == 1
    assert isinstance(override_events[0], RecruiterDecisionRecordedEvent)


@pytest.mark.unit
def test_identity_governance_tenant_quotas_and_gdpr_erasure() -> None:
    tid = TenantId.generate()
    tenant = TenantAccount(
        tenant_id=tid,
        organization_slug="acme-robotics",
        display_name="Acme Robotics Inc.",
        quota_policy=QuotaPolicy(
            max_concurrent_batches=1,
            max_files_per_batch=100,
            max_monthly_interviews=2,
        ),
        retention_policy=RetentionPolicy(pii_retention_days=90, erasure_sla_hours=24),
    )
    tenant.register_batch_started(50)
    assert tenant.active_batches_count == 1
    with pytest.raises(InvariantViolationError) as exc_batch:
        tenant.ensure_can_accept_batch(10)
    assert exc_batch.value.code == "TENANT_QUOTA_EXCEEDED"

    tenant.release_batch_slot()
    with pytest.raises(InvariantViolationError):
        tenant.ensure_can_accept_batch(150)

    tenant.consume_interview_quota()
    tenant.consume_interview_quota()
    with pytest.raises(InvariantViolationError):
        tenant.consume_interview_quota()

    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    consent = ConsentRecord(
        tenant_id=tid,
        candidate_id=generate_uuid7(),
        granted_scopes=[ConsentScope.AI_SCREENING, ConsentScope.INTEGRITY_TELEMETRY],
        retention_policy=tenant.retention_policy,
        granted_at=now,
    )
    assert consent.is_valid_for_processing(as_of=now + timedelta(days=10))
    assert not consent.is_valid_for_processing(as_of=now + timedelta(days=91))

    consent.request_erasure(
        reason="Candidate GDPR Art. 17 request", requested_at=now + timedelta(days=2)
    )
    assert consent.status.value == ConsentStatus.ERASURE_REQUESTED.value
    assert not consent.is_valid_for_processing(as_of=now + timedelta(days=3))
    erasure_events = consent.pull_domain_events()
    assert len(erasure_events) == 1
    assert isinstance(erasure_events[0], CandidateErasureRequestedEvent)
    assert erasure_events[0].erasure_deadline_at == now + timedelta(days=2, hours=24)

    consent.mark_crypto_shredded(shredded_at=now + timedelta(days=2, hours=4))
    assert consent.status.value == ConsentStatus.CRYPTO_SHREDDED.value
    with pytest.raises(InvalidStateTransitionError):
        consent.request_erasure()
