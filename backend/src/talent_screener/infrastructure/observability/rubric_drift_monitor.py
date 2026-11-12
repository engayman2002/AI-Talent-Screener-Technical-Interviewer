"""Statistical Rubric Drift & Calibration Engine: PSI, Kolmogorov-Smirnov & Cohen's Kappa."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from talent_screener.domain.contexts.evaluation_analytics.value_objects import (
    HiringRecommendationBand,
)


class PsiDriftStatus(StrEnum):
    """Canonical Population Stability Index severity classifications."""

    STABLE = "STABLE"
    MODERATE_DRIFT = "MODERATE_DRIFT"
    CRITICAL_DRIFT = "CRITICAL_DRIFT"


@dataclass(frozen=True, slots=True)
class PsiDriftResult:
    """Population Stability Index assessment result."""

    competency_label: str
    psi_value: float
    status: PsiDriftStatus
    bucket_count: int


@dataclass(frozen=True, slots=True)
class KsDriftResult:
    """Two-Sample Kolmogorov-Smirnov test result."""

    label: str
    d_statistic: float
    critical_value: float
    alpha: float
    baseline_n: int
    current_n: int
    drift_detected: bool


@dataclass(frozen=True, slots=True)
class KappaCalibrationResult:
    """Cohen's Kappa inter-rater calibration assessment between AI and Gold/Override verdicts."""

    label: str
    sample_count: int
    observed_agreement: float
    expected_agreement: float
    kappa: float
    weighted_kappa: float
    is_degraded: bool
    alert_code: str | None = None


_KS_C_ALPHA: dict[float, float] = {
    0.10: 1.22385,
    0.05: 1.35810,
    0.01: 1.62762,
}

_BAND_ORDER: dict[str, int] = {
    HiringRecommendationBand.NO_HIRE.value: 0,
    HiringRecommendationBand.BORDERLINE_HOLD.value: 1,
    HiringRecommendationBand.HIRE.value: 2,
    HiringRecommendationBand.STRONG_HIRE.value: 3,
}


def compute_psi(
    expected_distribution: Sequence[float],
    actual_distribution: Sequence[float],
    *,
    competency_label: str = "global_rubric",
    epsilon: float = 1e-6,
) -> PsiDriftResult:
    """Compute PSI = sum((P_b - Q_b) * ln((P_b + eps) / (Q_b + eps))) over aligned bins."""
    if len(expected_distribution) != len(actual_distribution) or not expected_distribution:
        raise ValueError(
            "expected_distribution and actual_distribution must be non-empty of equal length."
        )
    if any(x < 0.0 for x in expected_distribution) or any(x < 0.0 for x in actual_distribution):
        raise ValueError("Distribution proportions cannot be negative.")

    sum_q = sum(expected_distribution)
    sum_p = sum(actual_distribution)
    if sum_q <= 0.0 or sum_p <= 0.0:
        raise ValueError("Distributions must have strictly positive total mass.")

    psi = 0.0
    for q_raw, p_raw in zip(expected_distribution, actual_distribution, strict=True):
        q_b = q_raw / sum_q
        p_b = p_raw / sum_p
        psi += (p_b - q_b) * math.log((p_b + epsilon) / (q_b + epsilon))

    psi = max(0.0, round(psi, 6))
    if psi < 0.10:
        status = PsiDriftStatus.STABLE
    elif psi < 0.25:
        status = PsiDriftStatus.MODERATE_DRIFT
    else:
        status = PsiDriftStatus.CRITICAL_DRIFT

    return PsiDriftResult(
        competency_label=competency_label,
        psi_value=psi,
        status=status,
        bucket_count=len(expected_distribution),
    )


def compute_psi_from_scores(
    baseline_scores: Sequence[float],
    current_scores: Sequence[float],
    *,
    num_bins: int = 5,
    min_val: float = 0.0,
    max_val: float = 5.0,
    competency_label: str = "global_rubric",
) -> PsiDriftResult:
    """Bucketize raw competency mastery scores cleanly into ``num_bins`` and compute PSI."""
    if not baseline_scores or not current_scores:
        raise ValueError("Both baseline_scores and current_scores must be non-empty.")
    if num_bins < 2 or max_val <= min_val:
        raise ValueError("Invalid histogram bin specification.")

    width = (max_val - min_val) / float(num_bins)

    def _hist(values: Sequence[float]) -> list[float]:
        counts = [0.0] * num_bins
        for v in values:
            idx = int((v - min_val) / width)
            idx = max(0, min(num_bins - 1, idx))
            counts[idx] += 1.0
        total = float(len(values))
        return [c / total for c in counts]

    return compute_psi(
        _hist(baseline_scores),
        _hist(current_scores),
        competency_label=competency_label,
    )


def compute_ks_two_sample(
    baseline_samples: Sequence[float],
    current_samples: Sequence[float],
    *,
    alpha: float = 0.05,
    label: str = "score_cdf",
) -> KsDriftResult:
    """Compute exact two-sample Kolmogorov-Smirnov statistic D_KS = sup|F_1(x) - F_2(x)|."""
    n1 = len(baseline_samples)
    n2 = len(current_samples)
    if n1 == 0 or n2 == 0:
        raise ValueError("Both baseline_samples and current_samples must be non-empty.")

    s1 = sorted(baseline_samples)
    s2 = sorted(current_samples)
    combined = sorted(set(s1) | set(s2))

    i = 0
    j = 0
    max_diff = 0.0
    for x in combined:
        while i < n1 and s1[i] <= x:
            i += 1
        while j < n2 and s2[j] <= x:
            j += 1
        cdf1 = i / float(n1)
        cdf2 = j / float(n2)
        diff = abs(cdf1 - cdf2)
        if diff > max_diff:
            max_diff = diff

    c_alpha = _KS_C_ALPHA.get(alpha, 1.35810)
    crit = c_alpha * math.sqrt((n1 + n2) / float(n1 * n2))
    d_rounded = round(max_diff, 6)
    crit_rounded = round(crit, 6)
    return KsDriftResult(
        label=label,
        d_statistic=d_rounded,
        critical_value=crit_rounded,
        alpha=alpha,
        baseline_n=n1,
        current_n=n2,
        drift_detected=d_rounded > crit_rounded,
    )


def compute_cohens_kappa(
    rater_a: Sequence[str],
    rater_b: Sequence[str],
    *,
    categories: Sequence[str] | None = None,
    degraded_threshold: float = 0.70,
    label: str = "recommendation_calibration",
) -> KappaCalibrationResult:
    """Compute unweighted Cohen's Kappa and Quadratic Weighted Kappa (kappa_w)."""
    if len(rater_a) != len(rater_b) or not rater_a:
        raise ValueError("rater_a and rater_b must be non-empty sequences of equal length.")

    if categories is not None:
        cats = list(dict.fromkeys(categories))
    else:
        ordered = [
            HiringRecommendationBand.NO_HIRE.value,
            HiringRecommendationBand.BORDERLINE_HOLD.value,
            HiringRecommendationBand.HIRE.value,
            HiringRecommendationBand.STRONG_HIRE.value,
        ]
        seen = set(rater_a) | set(rater_b)
        cats = ordered if seen.issubset(set(ordered)) else sorted(seen)

    k = len(cats)
    idx_map = {cat: idx for idx, cat in enumerate(cats)}
    matrix = [[0 for _ in range(k)] for _ in range(k)]
    for a_val, b_val in zip(rater_a, rater_b, strict=True):
        if a_val not in idx_map or b_val not in idx_map:
            raise ValueError(f"Unknown category encountered: {a_val!r} vs {b_val!r}")
        matrix[idx_map[a_val]][idx_map[b_val]] += 1

    n = float(len(rater_a))
    row_totals = [sum(row) for row in matrix]
    col_totals = [sum(matrix[r][c] for r in range(k)) for c in range(k)]

    p_o = sum(matrix[i][i] for i in range(k)) / n
    p_e = sum((row_totals[i] * col_totals[i]) for i in range(k)) / (n * n)
    if (1.0 - p_e) <= 1e-12:
        kappa = 1.0 if p_o >= 1.0 - 1e-12 else 0.0
    else:
        kappa = (p_o - p_e) / (1.0 - p_e)

    # Quadratic Weighted Kappa over ordered indices
    if k <= 1:
        weighted_kappa = kappa
    else:
        denom_span = float((k - 1) ** 2)
        obs_w = 0.0
        exp_w = 0.0
        for i in range(k):
            for j in range(k):
                w = ((i - j) ** 2) / denom_span
                obs_w += w * (matrix[i][j] / n)
                exp_w += w * ((row_totals[i] / n) * (col_totals[j] / n))
        weighted_kappa = 1.0 - (obs_w / exp_w) if exp_w > 1e-12 else 1.0

    k_round = round(kappa, 6)
    kw_round = round(weighted_kappa, 6)
    degraded = k_round < degraded_threshold
    return KappaCalibrationResult(
        label=label,
        sample_count=len(rater_a),
        observed_agreement=round(p_o, 6),
        expected_agreement=round(p_e, 6),
        kappa=k_round,
        weighted_kappa=kw_round,
        is_degraded=degraded,
        alert_code="RUBRIC_CALIBRATION_DEGRADED" if degraded else None,
    )


class RubricDriftMonitor:
    """Stateful Phase 8 Rubric Drift & Calibration Monitor exporting ops telemetry."""

    def __init__(self, *, kappa_alert_threshold: float = 0.70) -> None:
        self._kappa_threshold = kappa_alert_threshold
        self._psi_results: dict[str, PsiDriftResult] = {}
        self._ks_results: dict[str, KsDriftResult] = {}
        self._kappa_results: dict[str, KappaCalibrationResult] = {}

    def evaluate_psi(
        self,
        *,
        competency_label: str,
        baseline_distribution: Sequence[float],
        current_distribution: Sequence[float],
    ) -> PsiDriftResult:
        res = compute_psi(
            baseline_distribution,
            current_distribution,
            competency_label=competency_label,
        )
        self._psi_results[competency_label] = res
        return res

    def evaluate_ks(
        self,
        *,
        label: str,
        baseline_scores: Sequence[float],
        current_scores: Sequence[float],
        alpha: float = 0.05,
    ) -> KsDriftResult:
        res = compute_ks_two_sample(baseline_scores, current_scores, alpha=alpha, label=label)
        self._ks_results[label] = res
        return res

    def evaluate_kappa(
        self,
        *,
        label: str,
        ai_verdicts: Sequence[str],
        reference_verdicts: Sequence[str],
    ) -> KappaCalibrationResult:
        res = compute_cohens_kappa(
            ai_verdicts,
            reference_verdicts,
            degraded_threshold=self._kappa_threshold,
            label=label,
        )
        self._kappa_results[label] = res
        return res

    def summarize(self) -> dict[str, object]:
        """Serialize latest PSI, Kolmogorov-Smirnov & Cohen's Kappa calibration results."""
        any_alert = (
            any(r.status is PsiDriftStatus.CRITICAL_DRIFT for r in self._psi_results.values())
            or any(r.drift_detected for r in self._ks_results.values())
            or any(r.is_degraded for r in self._kappa_results.values())
        )
        return {
            "has_active_drift_alert": any_alert,
            "psi_reports": {
                k: {
                    "psi": r.psi_value,
                    "status": r.status.value,
                    "buckets": r.bucket_count,
                }
                for k, r in self._psi_results.items()
            },
            "ks_reports": {
                k: {
                    "d_statistic": r.d_statistic,
                    "critical_value": r.critical_value,
                    "alpha": r.alpha,
                    "drift_detected": r.drift_detected,
                }
                for k, r in self._ks_results.items()
            },
            "kappa_reports": {
                k: {
                    "kappa": r.kappa,
                    "weighted_kappa": r.weighted_kappa,
                    "sample_count": r.sample_count,
                    "is_degraded": r.is_degraded,
                    "alert_code": r.alert_code,
                }
                for k, r in self._kappa_results.items()
            },
        }
