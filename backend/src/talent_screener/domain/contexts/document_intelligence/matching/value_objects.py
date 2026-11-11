"""Semantic Matching Engine Value Objects."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.exceptions import (
    InvalidMatchWeightsError,
)
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    require_finite_float,
    require_float_range,
    require_int_range,
    require_non_empty_str,
    require_non_nil_uuid,
)
from talent_screener.domain.shared.value_objects import ValueObject

CONVEX_WEIGHT_TOLERANCE: float = 1e-6


class CandidateTier(StrEnum):
    """Automated candidate screening tier classification."""

    FAST_TRACK = "FAST_TRACK"
    INTERVIEW = "INTERVIEW"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    REJECTED = "REJECTED"
    GATEKEEPER_REJECTED = "GATEKEEPER_REJECTED"


class GapType(StrEnum):
    """Classification of epistemic gaps feeding adaptive interview questioning."""

    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    BELOW_REQUIRED_DEPTH = "BELOW_REQUIRED_DEPTH"
    STALE_EXPERIENCE = "STALE_EXPERIENCE"
    UNVERIFIED_CLAIM = "UNVERIFIED_CLAIM"


@dataclass(frozen=True, slots=True)
class RecencyDecayFactor(ValueObject):
    """Exponential experience recency decay model: exp(-lambda_recency * months / 12)."""

    lambda_rate: float = math.log(2.0) / 3.0

    def __post_init__(self) -> None:
        rate = require_finite_float(
            self.lambda_rate,
            "lambda_rate",
            code="INVALID_RECENCY_DECAY_RATE",
        )
        if rate < 0.0:
            raise InvariantViolationError(
                f"lambda_rate must be non-negative; got {rate}.",
                code="INVALID_RECENCY_DECAY_RATE",
            )
        object.__setattr__(self, "lambda_rate", rate)

    @classmethod
    def from_half_life_years(cls, half_life_years: float) -> RecencyDecayFactor:
        """Construct RecencyDecayFactor from half-life tau_{1/2} in years."""
        half_life = require_finite_float(
            half_life_years,
            "half_life_years",
            code="INVALID_RECENCY_HALF_LIFE",
        )
        if half_life <= 0.0:
            raise InvariantViolationError(
                f"half_life_years must be strictly positive; got {half_life}.",
                code="INVALID_RECENCY_HALF_LIFE",
            )
        return cls(lambda_rate=math.log(2.0) / half_life)

    def decay_for_months(self, months_elapsed: int) -> float:
        """Compute exp(-lambda_rate * months_elapsed / 12.0) in (0.0, 1.0]."""
        require_int_range(months_elapsed, "months_elapsed", 0, 720, code="INVALID_RECENCY_MONTHS")
        return math.exp(-self.lambda_rate * (months_elapsed / 12.0))


@dataclass(frozen=True, slots=True)
class HybridMatchVector(ValueObject):
    """Convex combination alpha*dense + beta*sparse + gamma*recency gated by G_hard in {0, 1}."""

    dense_similarity: float
    sparse_bm25_score: float
    experience_alignment: float = 1.0
    alpha_dense: float = 0.55
    beta_sparse: float = 0.25
    gamma_recency: float = 0.20
    gatekeeper_multiplier: float = 1.0
    recency_decay: RecencyDecayFactor = field(default_factory=RecencyDecayFactor)
    recency_months_elapsed: int = 0

    def __post_init__(self) -> None:
        dense = require_float_range(
            self.dense_similarity, "dense_similarity", 0.0, 1.0, code="INVALID_SIMILARITY_SCORE"
        )
        sparse = require_float_range(
            self.sparse_bm25_score, "sparse_bm25_score", 0.0, 1.0, code="INVALID_SIMILARITY_SCORE"
        )
        exp_align = require_float_range(
            self.experience_alignment,
            "experience_alignment",
            0.0,
            1.0,
            code="INVALID_EXPERIENCE_ALIGNMENT",
        )
        alpha = require_float_range(
            self.alpha_dense, "alpha_dense", 0.0, 1.0, code="INVALID_HYBRID_WEIGHTS"
        )
        beta = require_float_range(
            self.beta_sparse, "beta_sparse", 0.0, 1.0, code="INVALID_HYBRID_WEIGHTS"
        )
        gamma = require_float_range(
            self.gamma_recency, "gamma_recency", 0.0, 1.0, code="INVALID_HYBRID_WEIGHTS"
        )
        weight_sum = alpha + beta + gamma
        if abs(weight_sum - 1.0) > CONVEX_WEIGHT_TOLERANCE:
            raise InvalidMatchWeightsError(
                f"Hybrid match convex weights (alpha + beta + gamma) must equal 1.0; "
                f"got {weight_sum:.9f}.",
            )
        gk = require_finite_float(
            self.gatekeeper_multiplier,
            "gatekeeper_multiplier",
            code="INVALID_GATEKEEPER_MULTIPLIER",
        )
        if gk not in (0.0, 1.0):
            raise InvariantViolationError(
                f"gatekeeper_multiplier must be binary 0.0 or 1.0; got {gk}.",
                code="INVALID_GATEKEEPER_MULTIPLIER",
            )
        if not isinstance(self.recency_decay, RecencyDecayFactor):
            raise InvariantViolationError(
                "recency_decay must be a RecencyDecayFactor.",
                code="INVALID_RECENCY_DECAY",
            )
        require_int_range(
            self.recency_months_elapsed,
            "recency_months_elapsed",
            0,
            720,
            code="INVALID_RECENCY_MONTHS",
        )
        object.__setattr__(self, "dense_similarity", dense)
        object.__setattr__(self, "sparse_bm25_score", sparse)
        object.__setattr__(self, "experience_alignment", exp_align)
        object.__setattr__(self, "alpha_dense", alpha)
        object.__setattr__(self, "beta_sparse", beta)
        object.__setattr__(self, "gamma_recency", gamma)
        object.__setattr__(self, "gatekeeper_multiplier", gk)

    @property
    def recency_multiplier(self) -> float:
        """Exponential recency decay factor for the elapsed months."""
        return self.recency_decay.decay_for_months(self.recency_months_elapsed)

    @property
    def recency_adjusted_experience(self) -> float:
        """Experience alignment attenuated by exponential recency decay."""
        return self.experience_alignment * self.recency_multiplier

    @property
    def raw_convex_score(self) -> float:
        """Weighted convex combination in [0.0, 1.0] prior to gatekeeper multiplier."""
        return (
            self.alpha_dense * self.dense_similarity
            + self.beta_sparse * self.sparse_bm25_score
            + self.gamma_recency * self.recency_adjusted_experience
        )

    @property
    def composite_score(self) -> float:
        """Final gatekeeper-multiplied match score on [0.0, 100.0] scale."""
        return 100.0 * self.gatekeeper_multiplier * self.raw_convex_score


@dataclass(frozen=True, slots=True)
class EpistemicSkillGap(ValueObject):
    """Prioritized competency deficit or unverified claim targeting live interview probes."""

    competency_id: UUID
    canonical_skill_id: str
    gap_type: GapType
    probe_priority: float
    suspect_claim_summary: str = ""

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.competency_id, "competency_id", code="INVALID_COMPETENCY_ID")
        cleaned_skill = require_non_empty_str(
            self.canonical_skill_id,
            "canonical_skill_id",
            max_len=120,
            code="INVALID_SKILL_ID",
        )
        if not isinstance(self.gap_type, GapType):
            raise InvariantViolationError(
                "gap_type must be a GapType enum.",
                code="INVALID_GAP_TYPE",
            )
        priority = require_float_range(
            self.probe_priority,
            "probe_priority",
            0.0,
            1.0,
            code="INVALID_PROBE_PRIORITY",
        )
        cleaned_summary = self.suspect_claim_summary.strip()
        if len(cleaned_summary) > 250:
            cleaned_summary = require_non_empty_str(
                cleaned_summary,
                "suspect_claim_summary",
                max_len=250,
                code="INVALID_GAP_SUMMARY",
            )
        object.__setattr__(self, "canonical_skill_id", cleaned_skill)
        object.__setattr__(self, "probe_priority", priority)
        object.__setattr__(self, "suspect_claim_summary", cleaned_summary)


@dataclass(frozen=True, slots=True)
class CompetencyEvaluationSpec(ValueObject):
    """Published-language competency rubric specification consumed by matching service."""

    competency_id: UUID
    canonical_skill_id: str
    weight: float
    target_depth: int = 3
    is_gatekeeper: bool = False
    min_applied_months: int = 0
    strict_gatekeeper: bool = True

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.competency_id, "competency_id", code="INVALID_COMPETENCY_ID")
        cleaned_skill = require_non_empty_str(
            self.canonical_skill_id,
            "canonical_skill_id",
            max_len=120,
            code="INVALID_SKILL_ID",
        )
        weight = require_float_range(
            self.weight,
            "weight",
            0.0,
            1.0,
            inclusive_min=False,
            inclusive_max=True,
            code="INVALID_COMPETENCY_WEIGHT",
        )
        require_int_range(self.target_depth, "target_depth", 1, 5, code="INVALID_TARGET_DEPTH")
        require_int_range(
            self.min_applied_months, "min_applied_months", 0, 120, code="INVALID_MIN_MONTHS"
        )
        object.__setattr__(self, "canonical_skill_id", cleaned_skill)
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True, slots=True)
class ChunkEvidenceSample(ValueObject):
    """Single retrieved candidate CV evidence chunk used in hybrid matching."""

    dense_similarity: float
    sparse_bm25_score: float
    recency_months_delta: int = 0
    evidentiary_depth: int = 3
    verbatim_excerpt: str = ""

    def __post_init__(self) -> None:
        dense = require_float_range(
            self.dense_similarity, "dense_similarity", 0.0, 1.0, code="INVALID_SIMILARITY_SCORE"
        )
        sparse = require_float_range(
            self.sparse_bm25_score, "sparse_bm25_score", 0.0, 1.0, code="INVALID_SIMILARITY_SCORE"
        )
        require_int_range(
            self.recency_months_delta,
            "recency_months_delta",
            0,
            720,
            code="INVALID_RECENCY_MONTHS",
        )
        require_int_range(
            self.evidentiary_depth,
            "evidentiary_depth",
            1,
            4,
            code="INVALID_EVIDENTIARY_DEPTH",
        )
        object.__setattr__(self, "dense_similarity", dense)
        object.__setattr__(self, "sparse_bm25_score", sparse)
        object.__setattr__(self, "verbatim_excerpt", self.verbatim_excerpt.strip()[:500])


@dataclass(frozen=True, slots=True)
class MatchThresholds(ValueObject):
    """Matching tier threshold cutoffs satisfying fast_track > interview > review."""

    fast_track_threshold: float = 88.0
    interview_threshold: float = 75.0
    review_threshold: float = 58.0

    def __post_init__(self) -> None:
        fast = require_float_range(
            self.fast_track_threshold,
            "fast_track_threshold",
            0.0,
            100.0,
            code="INVALID_TIER_THRESHOLDS",
        )
        interview = require_float_range(
            self.interview_threshold,
            "interview_threshold",
            0.0,
            100.0,
            code="INVALID_TIER_THRESHOLDS",
        )
        review = require_float_range(
            self.review_threshold,
            "review_threshold",
            0.0,
            100.0,
            code="INVALID_TIER_THRESHOLDS",
        )
        if not (fast > interview > review):
            raise InvariantViolationError(
                "MatchThresholds must satisfy fast_track > interview > review.",
                code="INVALID_TIER_THRESHOLDS",
            )
        object.__setattr__(self, "fast_track_threshold", fast)
        object.__setattr__(self, "interview_threshold", interview)
        object.__setattr__(self, "review_threshold", review)
