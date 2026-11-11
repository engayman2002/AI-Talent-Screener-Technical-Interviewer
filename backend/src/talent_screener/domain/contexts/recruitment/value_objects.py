"""Recruitment Management Bounded Context Value Objects."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from talent_screener.domain.contexts.recruitment.exceptions import RubricWeightSumError
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    require_finite_float,
    require_float_range,
    require_int_range,
    require_non_empty_str,
)
from talent_screener.domain.shared.value_objects import ValueObject

WEIGHT_SUM_TOLERANCE: float = 1e-6
MAX_HARD_SKILL_GATEKEEPERS: int = 5


class RequisitionStatus(StrEnum):
    """Lifecycle states for a JobRequisition aggregate."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class SeniorityLevel(StrEnum):
    """Canonical engineering seniority tiers anchoring target rubric depth."""

    JUNIOR = "L3_JUNIOR"
    MID = "L4_MID"
    SENIOR = "L5_SENIOR"
    STAFF = "L6_STAFF"
    PRINCIPAL = "L7_PRINCIPAL"

    @property
    def baseline_depth(self) -> int:
        """Default expected CompetencyDepthLevel (1..5) for this seniority band."""
        return {
            SeniorityLevel.JUNIOR: 2,
            SeniorityLevel.MID: 3,
            SeniorityLevel.SENIOR: 4,
            SeniorityLevel.STAFF: 4,
            SeniorityLevel.PRINCIPAL: 5,
        }[self]

    @property
    def min_experience_months(self) -> int:
        """Minimum expected professional months of experience."""
        return {
            SeniorityLevel.JUNIOR: 12,
            SeniorityLevel.MID: 36,
            SeniorityLevel.SENIOR: 60,
            SeniorityLevel.STAFF: 96,
            SeniorityLevel.PRINCIPAL: 120,
        }[self]


@dataclass(frozen=True, slots=True)
class CompetencyWeightVector(ValueObject):
    """Normalized distribution of competency weights summing to 1.0 +/- 1e-6."""

    weights: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if not self.weights:
            raise RubricWeightSumError(
                "CompetencyWeightVector must contain at least one competency weight.",
                code="EMPTY_COMPETENCY_WEIGHTS",
            )
        seen_keys: set[str] = set()
        normalized_items: list[tuple[str, float]] = []
        total = 0.0

        for raw_key, raw_weight in self.weights:
            key = require_non_empty_str(
                raw_key,
                "competency_key",
                max_len=128,
                code="INVALID_COMPETENCY_KEY",
            )
            if key in seen_keys:
                raise InvariantViolationError(
                    f"Duplicate competency key in CompetencyWeightVector: {key!r}.",
                    code="DUPLICATE_COMPETENCY_KEY",
                )
            seen_keys.add(key)
            weight = require_float_range(
                raw_weight,
                f"weight[{key}]",
                0.0,
                1.0,
                inclusive_min=False,
                inclusive_max=True,
                code="INVALID_COMPETENCY_WEIGHT",
            )
            normalized_items.append((key, weight))
            total += weight

        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise RubricWeightSumError(
                f"Competency weights must sum to 1.0 (+/- {WEIGHT_SUM_TOLERANCE}); "
                f"got {total:.9f}.",
            )

        object.__setattr__(self, "weights", tuple(normalized_items))

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, float]) -> CompetencyWeightVector:
        """Construct a CompetencyWeightVector from a key -> weight mapping."""
        return cls(tuple(mapping.items()))

    @classmethod
    def normalize_from_raw(cls, raw_scores: Mapping[str, float]) -> CompetencyWeightVector:
        """Dynamically normalize positive raw importance scores into a unit weight vector."""
        if not raw_scores:
            raise RubricWeightSumError(
                "Cannot normalize empty raw importance scores.",
                code="EMPTY_COMPETENCY_WEIGHTS",
            )
        validated: list[tuple[str, float]] = []
        total_raw = 0.0
        for raw_key, raw_val in raw_scores.items():
            key = require_non_empty_str(
                raw_key,
                "competency_key",
                max_len=128,
                code="INVALID_COMPETENCY_KEY",
            )
            val = require_finite_float(raw_val, f"raw_score[{key}]", code="INVALID_RAW_WEIGHT")
            if val <= 0.0:
                raise InvariantViolationError(
                    f"Raw importance score for {key!r} must be strictly positive; got {val}.",
                    code="INVALID_RAW_WEIGHT",
                )
            validated.append((key, val))
            total_raw += val

        normalized = tuple((key, val / total_raw) for key, val in validated)
        return cls(normalized)

    def as_mapping(self) -> Mapping[str, float]:
        """Return a read-only mapping view of competency weights."""
        return MappingProxyType(dict(self.weights))

    def weight_for(self, key: str) -> float:
        """Return the weight assigned to a competency key."""
        mapping = dict(self.weights)
        if key not in mapping:
            raise InvariantViolationError(
                f"Competency key {key!r} not present in weight vector.",
                code="COMPETENCY_NOT_FOUND",
            )
        return mapping[key]


@dataclass(frozen=True, slots=True)
class GatekeeperRule(ValueObject):
    """Non-negotiable hard-skill gatekeeper qualification rule."""

    canonical_skill_id: str
    min_applied_months: int = 0
    strict_disqualification: bool = True

    def __post_init__(self) -> None:
        cleaned_skill = require_non_empty_str(
            self.canonical_skill_id,
            "canonical_skill_id",
            max_len=120,
            code="INVALID_SKILL_ID",
        )
        object.__setattr__(self, "canonical_skill_id", cleaned_skill)
        require_int_range(
            self.min_applied_months,
            "min_applied_months",
            0,
            120,
            code="INVALID_MIN_MONTHS",
        )
        if not isinstance(self.strict_disqualification, bool):
            raise InvariantViolationError(
                "strict_disqualification must be a boolean.",
                code="INVALID_GATEKEEPER_POLICY",
            )


@dataclass(frozen=True, slots=True)
class HardSkillGatekeeperSet(ValueObject):
    """Bounded immutable set of hard-skill gatekeepers attached to a JobRequisition."""

    rules: frozenset[GatekeeperRule]

    def __post_init__(self) -> None:
        if len(self.rules) > MAX_HARD_SKILL_GATEKEEPERS:
            raise InvariantViolationError(
                f"At most {MAX_HARD_SKILL_GATEKEEPERS} hard-skill gatekeepers are allowed; "
                f"got {len(self.rules)}.",
                code="GATEKEEPER_LIMIT_EXCEEDED",
            )
        skill_ids = [rule.canonical_skill_id for rule in self.rules]
        if len(skill_ids) != len(set(skill_ids)):
            raise InvariantViolationError(
                "Gatekeeper rules must reference distinct canonical_skill_id values.",
                code="DUPLICATE_GATEKEEPER_SKILL",
            )

    @classmethod
    def from_skill_ids(
        cls,
        skill_ids: Iterable[str],
        *,
        min_applied_months: int = 0,
        strict_disqualification: bool = True,
    ) -> HardSkillGatekeeperSet:
        """Create a HardSkillGatekeeperSet from an iterable of canonical skill identifiers."""
        rules = frozenset(
            GatekeeperRule(
                canonical_skill_id=skill_id,
                min_applied_months=min_applied_months,
                strict_disqualification=strict_disqualification,
            )
            for skill_id in skill_ids
        )
        return cls(rules=rules)

    @property
    def skill_ids(self) -> frozenset[str]:
        """Canonical skill IDs enforced by this gatekeeper set."""
        return frozenset(rule.canonical_skill_id for rule in self.rules)

    @property
    def is_empty(self) -> bool:
        """True when no gatekeepers are configured."""
        return len(self.rules) == 0


@dataclass(frozen=True, slots=True)
class TierThresholdConfig(ValueObject):
    """Candidate classification cutoffs satisfying tau_fast > tau_interview > tau_review."""

    fast_track_threshold: float = 88.0
    interview_threshold: float = 75.0
    review_threshold: float = 58.0

    def __post_init__(self) -> None:
        tau_fast = require_float_range(
            self.fast_track_threshold,
            "fast_track_threshold",
            0.0,
            100.0,
            code="INVALID_TIER_THRESHOLDS",
        )
        tau_interview = require_float_range(
            self.interview_threshold,
            "interview_threshold",
            0.0,
            100.0,
            code="INVALID_TIER_THRESHOLDS",
        )
        tau_review = require_float_range(
            self.review_threshold,
            "review_threshold",
            0.0,
            100.0,
            code="INVALID_TIER_THRESHOLDS",
        )
        if not (tau_fast > tau_interview > tau_review):
            raise InvariantViolationError(
                "Thresholds must strictly satisfy fast_track > interview > review "
                f"(got {tau_fast}, {tau_interview}, {tau_review}).",
                code="INVALID_TIER_THRESHOLDS",
            )
        object.__setattr__(self, "fast_track_threshold", tau_fast)
        object.__setattr__(self, "interview_threshold", tau_interview)
        object.__setattr__(self, "review_threshold", tau_review)
