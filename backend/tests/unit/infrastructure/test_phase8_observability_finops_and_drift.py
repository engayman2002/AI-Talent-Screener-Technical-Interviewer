"""Unit tests for Phase 8 ADR-013 OTel W3C Tracing, FinOps Routing & Rubric Drift Math."""

from __future__ import annotations

from uuid import uuid4

import pytest

from talent_screener.application.common.exceptions import QuotaExceededError
from talent_screener.domain.contexts.evaluation_analytics.value_objects import (
    HiringRecommendationBand,
)
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.observability import (
    ModelTier,
    MultiTenantFinOpsController,
    PsiDriftStatus,
    RubricDriftMonitor,
    StructuredOpenTelemetryTracerAdapter,
    compute_cohens_kappa,
    compute_ks_two_sample,
    compute_psi,
    compute_psi_from_scores,
    parse_traceparent,
)


@pytest.mark.unit
def test_otel_tracer_w3c_nesting_latency_percentiles_and_pii_redaction() -> None:
    tick = 100.0

    def _clock() -> float:
        return tick

    tracer = StructuredOpenTelemetryTracerAdapter(clock_fn=_clock)
    tenant_id = TenantId.generate()

    # Valid vs invalid W3C traceparent
    assert parse_traceparent("00-00000000000000000000000000000000-1111111111111111-01") is None
    incoming_tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    parsed = parse_traceparent(incoming_tp)
    assert parsed is not None
    assert parsed.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"

    # Nested spans: ws.turn -> langgraph.node -> gen_ai.chat with PII scrambling
    with tracer.start_span(
        name="ws.turn",
        tenant_id=tenant_id,
        correlation_id="corr-phase8",
        traceparent=incoming_tp,
        attributes={
            "candidate_note": "Reach me at candidate.secret@example.com or 555-867-5309",
            "full_name": "Alice Secret",
            "authorization": "Bearer eeeeeeeeee.pppppppppp.ssssssssss",
        },
    ) as parent:
        tick += 0.015  # 15ms before child start
        with tracer.start_span(
            name="gen_ai.chat",
            tenant_id=tenant_id,
            correlation_id="corr-phase8",
            attributes={"gen_ai.request.model": "gpt-4o"},
        ):
            tick += 0.120  # 120ms child span
        tick += 0.010

    assert len(tracer.spans) == 2
    child_span, parent_span = tracer.spans[0], tracer.spans[1]
    assert parent_span.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert parent_span.parent_span_id == "00f067aa0ba902b7"
    assert child_span.trace_id == parent_span.trace_id
    assert child_span.parent_span_id == parent.span_id
    assert child_span.duration_ms == pytest.approx(120.0, abs=0.5)
    assert parent_span.duration_ms == pytest.approx(145.0, abs=0.5)

    # Zero raw PII anywhere in span attributes or serialized payload
    serialized_parent = str(parent_span.to_dict())
    assert "candidate.secret@example.com" not in serialized_parent
    assert "555-867-5309" not in serialized_parent
    assert "Alice Secret" not in serialized_parent
    assert "eeeeeeeeee.pppppppppp.ssssssssss" not in serialized_parent
    assert "[REDACTED_EMAIL:" in serialized_parent
    assert "[REDACTED_KEY:" in serialized_parent

    # Emit varied latency samples to verify P50, P95, P99 calculation
    for ms in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 250.0):
        with tracer.start_span(
            name="guardrail.inspect",
            tenant_id=tenant_id,
            correlation_id="lat",
        ):
            tick += ms / 1000.0

    pct = tracer.compute_latency_percentiles(span_name="guardrail.inspect")
    assert pct.count == 10
    assert pct.p50_ms == pytest.approx(55.0, abs=1.0)
    assert pct.p99_ms > pct.p95_ms > pct.p50_ms


@pytest.mark.unit
def test_finops_controller_downgrades_at_80_pct_and_halts_at_100_pct() -> None:
    finops = MultiTenantFinOpsController(
        session_token_quota=1_000,
        default_tenant_token_quota=5_000,
        soft_downgrade_ratio=0.80,
    )
    tenant_id = TenantId.generate()
    session_id = uuid4()

    # Initial routing -> PRIMARY_FRONTIER_MODEL
    d1 = finops.select_model_tier(tenant_id=tenant_id, session_id=session_id, projected_tokens=100)
    assert d1.selected_tier is ModelTier.PRIMARY_FRONTIER_MODEL
    assert not d1.downgraded_due_to_soft_cap

    # Record 820 tokens (82% of 1000 session quota) -> soft downgrade to COST_OPTIMIZED_FAST_MODEL
    finops.record_usage(
        tenant_id=tenant_id,
        session_id=session_id,
        node_name="EvaluationNode",
        cached_input_tokens=600,
        uncached_input_tokens=120,
        output_tokens=100,
    )
    d2 = finops.select_model_tier(tenant_id=tenant_id, session_id=session_id, projected_tokens=50)
    assert d2.selected_tier is ModelTier.COST_OPTIMIZED_FAST_MODEL
    assert d2.downgraded_due_to_soft_cap
    assert d2.session_utilization_ratio >= 0.80

    entry2 = finops.record_usage(
        tenant_id=tenant_id,
        session_id=session_id,
        node_name="QuestionGeneratorNode",
        cached_input_tokens=100,
        uncached_input_tokens=20,
        output_tokens=30,
    )
    assert entry2.tier is ModelTier.COST_OPTIMIZED_FAST_MODEL

    # Pushing beyond 1000 (970 + 50 > 1000) raises FINOPS_HARD_BUDGET_EXCEEDED
    with pytest.raises(QuotaExceededError) as exc_info:
        finops.select_model_tier(tenant_id=tenant_id, session_id=session_id, projected_tokens=50)
    assert exc_info.value.code == "FINOPS_HARD_BUDGET_EXCEEDED"

    summary = finops.summarize_ledger(tenant_id=tenant_id)
    assert summary["total_tokens"] == 970
    cache_ratio = summary["prefix_cache_hit_ratio"]
    cost_micro = summary["total_cost_micro_usd"]
    assert isinstance(cache_ratio, float) and cache_ratio > 0.80
    assert isinstance(cost_micro, int) and cost_micro > 0


@pytest.mark.unit
def test_statistical_rubric_drift_psi_ks_and_cohens_kappa_calculators() -> None:
    # 1. PSI classification: STABLE (<0.10), MODERATE (0.10..0.25), CRITICAL (>=0.25)
    stable = compute_psi([0.20, 0.20, 0.20, 0.20, 0.20], [0.21, 0.19, 0.20, 0.21, 0.19])
    assert stable.status is PsiDriftStatus.STABLE
    assert stable.psi_value < 0.10

    critical = compute_psi([0.20, 0.20, 0.20, 0.20, 0.20], [0.60, 0.20, 0.10, 0.05, 0.05])
    assert critical.status is PsiDriftStatus.CRITICAL_DRIFT
    assert critical.psi_value >= 0.25

    raw_psi = compute_psi_from_scores(
        [1.2, 2.4, 3.1, 3.8, 4.2] * 4,
        [1.1, 2.3, 3.2, 3.9, 4.3] * 4,
        competency_label="Python",
    )
    assert raw_psi.status is PsiDriftStatus.STABLE

    # 2. Kolmogorov-Smirnov D_KS: identical vs shifted cohort
    base = [float(i) / 40.0 for i in range(40)]
    same_ks = compute_ks_two_sample(base, base, alpha=0.05)
    assert same_ks.d_statistic == 0.0
    assert not same_ks.drift_detected

    shifted = [x + 0.65 for x in base]
    shifted_ks = compute_ks_two_sample(base, shifted, alpha=0.05)
    assert shifted_ks.d_statistic > shifted_ks.critical_value
    assert shifted_ks.drift_detected

    # 3. Cohen's Kappa calibration check (< 0.70 triggers RUBRIC_CALIBRATION_DEGRADED)
    gold = [
        HiringRecommendationBand.STRONG_HIRE.value,
        HiringRecommendationBand.HIRE.value,
        HiringRecommendationBand.BORDERLINE_HOLD.value,
        HiringRecommendationBand.NO_HIRE.value,
    ] * 5
    aligned = list(gold)
    good_k = compute_cohens_kappa(aligned, gold)
    assert good_k.kappa == 1.0
    assert not good_k.is_degraded

    drifted_ai = [HiringRecommendationBand.STRONG_HIRE.value] * 18 + [
        HiringRecommendationBand.NO_HIRE.value,
        HiringRecommendationBand.NO_HIRE.value,
    ]
    monitor = RubricDriftMonitor(kappa_alert_threshold=0.70)
    bad_k = monitor.evaluate_kappa(
        label="nightly_canary_50",
        ai_verdicts=drifted_ai,
        reference_verdicts=gold,
    )
    assert bad_k.kappa < 0.70
    assert bad_k.is_degraded
    assert bad_k.alert_code == "RUBRIC_CALIBRATION_DEGRADED"
    assert monitor.summarize()["has_active_drift_alert"] is True
