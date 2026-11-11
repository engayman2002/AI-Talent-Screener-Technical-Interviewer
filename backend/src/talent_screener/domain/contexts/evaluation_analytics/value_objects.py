"""Evaluation & Analytics Bounded Context Value Objects."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    clamp,
    require_float_range,
    require_int_range,
)
from talent_screener.domain.shared.value_objects import ValueObject


class HiringRecommendationBand(StrEnum):
    """Canonical hiring recommendation verdict tiers."""

    STRONG_HIRE = "STRONG_HIRE"
    HIRE = "HIRE"
    BORDERLINE_HOLD = "BORDERLINE_HOLD"
    NO_HIRE = "NO_HIRE"


@dataclass(frozen=True, slots=True)
class ConfidenceInterval(ValueObject):
    """Bounded epistemic uncertainty interval [lower, upper] inside [0.0, 1.0]."""

    lower: float
    upper: float

    def __post_init__(self) -> None:
        low = require_float_range(
            self.lower, "confidence_interval.lower", 0.0, 1.0, code="INVALID_CONFIDENCE_INTERVAL"
        )
        high = require_float_range(
            self.upper, "confidence_interval.upper", 0.0, 1.0, code="INVALID_CONFIDENCE_INTERVAL"
        )
        if low > high:
            raise InvariantViolationError(
                f"ConfidenceInterval requires lower <= upper; got [{low}, {high}].",
                code="INVALID_CONFIDENCE_INTERVAL",
            )
        object.__setattr__(self, "lower", low)
        object.__setattr__(self, "upper", high)

    @property
    def width(self) -> float:
        return self.upper - self.lower


@dataclass(frozen=True, slots=True)
class AuthenticityIndex(ValueObject):
    """Probabilistic authenticity metric I_auth in [0.0, 1.0] with asymmetric penalty math."""

    score: float
    confidence_interval: ConfidenceInterval | None = None

    def __post_init__(self) -> None:
        val = require_float_range(
            self.score, "authenticity_score", 0.0, 1.0, code="INVALID_AUTHENTICITY_INDEX"
        )
        object.__setattr__(self, "score", val)
        if self.confidence_interval is None:
            ci = ConfidenceInterval(
                lower=clamp(val - 0.05, 0.0, 1.0),
                upper=clamp(val + 0.05, 0.0, 1.0),
            )
            object.__setattr__(self, "confidence_interval", ci)
        elif not isinstance(self.confidence_interval, ConfidenceInterval):
            raise InvariantViolationError(
                "confidence_interval must be a ConfidenceInterval.",
                code="INVALID_CONFIDENCE_INTERVAL",
            )

    @staticmethod
    def compute_client_penalty(
        *,
        blur_count: int,
        blur_duration_seconds: float,
        external_paste: bool,
        keystroke_anomaly: bool,
        nu_blur: float = 0.08,
        nu_paste: float = 0.45,
        nu_burst: float = 0.35,
    ) -> float:
        """Compute P_client in [0.0, 1.0] per Phase 4 Eq. 3.2."""
        n_blur = require_int_range(blur_count, "blur_count", 0, code="INVALID_TELEMETRY_METRIC")
        dt_blur = require_float_range(
            blur_duration_seconds,
            "blur_duration_seconds",
            0.0,
            86400.0,
            code="INVALID_TELEMETRY_METRIC",
        )
        raw = (
            nu_blur * n_blur * math.log(1.0 + dt_blur)
            + (nu_paste if external_paste else 0.0)
            + (nu_burst if keystroke_anomaly else 0.0)
        )
        return min(1.0, raw)

    @staticmethod
    def compute_turn_suspicion_risk(
        *,
        p_client: float,
        p_cadence: float,
        p_stylo: float,
    ) -> float:
        """Compute corroborated R_turn in [0.0, 1.0] per Phase 4 Eq. 3.2."""
        pc = require_float_range(p_client, "p_client", 0.0, 1.0, code="INVALID_SUSPICION_SIGNAL")
        pd = require_float_range(p_cadence, "p_cadence", 0.0, 1.0, code="INVALID_SUSPICION_SIGNAL")
        ps = require_float_range(p_stylo, "p_stylo", 0.0, 1.0, code="INVALID_SUSPICION_SIGNAL")
        risk = 0.35 * pc + 0.30 * pd + 0.35 * ps + 0.40 * math.sqrt(pc * ps)
        return min(1.0, risk)

    def apply_turn_update(
        self,
        *,
        turn_suspicion_risk: float,
        socratic_verified: bool = False,
        lambda_drop: float = 0.22,
        lambda_rec: float = 0.08,
    ) -> AuthenticityIndex:
        """Apply asymmetric penalty/recovery update I_auth^(t) in [0.0, 1.0]."""
        r_turn = require_float_range(
            turn_suspicion_risk, "turn_suspicion_risk", 0.0, 1.0, code="INVALID_SUSPICION_RISK"
        )
        drop_rate = require_float_range(
            lambda_drop, "lambda_drop", 0.0, 1.0, code="INVALID_DECAY_PARAM"
        )
        rec_rate = require_float_range(
            lambda_rec, "lambda_rec", 0.0, 1.0, code="INVALID_DECAY_PARAM"
        )
        penalty = drop_rate * max(0.0, r_turn - 0.25)
        recovery = rec_rate if socratic_verified else 0.0
        updated = clamp(self.score - penalty + recovery, 0.0, 1.0)
        return AuthenticityIndex(score=updated)

    def authenticity_moderator(self) -> float:
        """Compute Phi_auth(I_auth) confidence moderator per Phase 4 Section 4.3."""
        if self.score >= 0.80:
            return 1.00
        if self.score >= 0.60:
            return 0.85 + 0.75 * (self.score - 0.60)
        return 0.50 * (self.score / 0.60)

    @property
    def is_high_risk(self) -> bool:
        """True when authenticity drops below the INV-EA-02 0.50 high-risk threshold."""
        return self.score < 0.50

    @property
    def requires_human_review(self) -> bool:
        """True when authenticity falls below 0.80 verified threshold."""
        return self.score < 0.80


@dataclass(frozen=True, slots=True)
class CompositeRubricScore(ValueObject):
    """Synthesized post-interview composite scores aligning technical & CV evaluations."""

    technical_interview_score: float
    cv_match_score: float
    authenticity_moderator: float
    recommendation_index: float

    def __post_init__(self) -> None:
        tech = require_float_range(
            self.technical_interview_score,
            "technical_interview_score",
            0.0,
            100.0,
            code="INVALID_RUBRIC_SCORE",
        )
        cv = require_float_range(
            self.cv_match_score,
            "cv_match_score",
            0.0,
            100.0,
            code="INVALID_RUBRIC_SCORE",
        )
        mod = require_float_range(
            self.authenticity_moderator,
            "authenticity_moderator",
            0.0,
            1.0,
            code="INVALID_AUTHENTICITY_MODERATOR",
        )
        rec = require_float_range(
            self.recommendation_index,
            "recommendation_index",
            0.0,
            100.0,
            code="INVALID_RUBRIC_SCORE",
        )
        expected_rec = clamp((0.25 * cv + 0.75 * tech) * mod, 0.0, 100.0)
        if abs(rec - expected_rec) > 1e-3:
            raise InvariantViolationError(
                f"recommendation_index ({rec:.4f}) diverges from expected ({expected_rec:.4f}).",
                code="COMPOSITE_SCORE_MISMATCH",
            )
        object.__setattr__(self, "technical_interview_score", tech)
        object.__setattr__(self, "cv_match_score", cv)
        object.__setattr__(self, "authenticity_moderator", mod)
        object.__setattr__(self, "recommendation_index", rec)

    @classmethod
    def from_components(
        cls,
        *,
        technical_interview_score: float,
        cv_match_score: float,
        authenticity_index: AuthenticityIndex,
    ) -> CompositeRubricScore:
        """Compute CompositeRubricScore via Phase 4 H_rec equation."""
        if not isinstance(authenticity_index, AuthenticityIndex):
            raise InvariantViolationError(
                "authenticity_index must be an AuthenticityIndex.",
                code="INVALID_AUTHENTICITY_INDEX",
            )
        mod = authenticity_index.authenticity_moderator()
        rec = clamp((0.25 * cv_match_score + 0.75 * technical_interview_score) * mod, 0.0, 100.0)
        return cls(
            technical_interview_score=technical_interview_score,
            cv_match_score=cv_match_score,
            authenticity_moderator=mod,
            recommendation_index=rec,
        )
