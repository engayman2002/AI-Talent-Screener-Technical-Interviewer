"""Live Interview Bounded Context Value Objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum

from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    require_float_range,
    require_int_range,
    require_non_empty_str,
)
from talent_screener.domain.shared.value_objects import (
    ConfidenceScore,
    SHA256Hash,
    ValueObject,
)

ETA_ACCURACY: float = 0.35
ETA_DEPTH: float = 0.30
ETA_TRADEOFF: float = 0.20
ETA_SPECIFICITY: float = 0.15


class DifficultyLevel(IntEnum):
    """Ordinal technical competency depth scale from L1 (foundational) to L5 (principal)."""

    L1 = 1
    L2 = 2
    L3 = 3
    L4 = 4
    L5 = 5

    L1_CONCEPTUAL = 1
    L2_APPLIED = 2
    L3_DEBUGGING = 3
    L4_TRADEOFFS = 4
    L5_ARCHITECTURAL = 5

    @classmethod
    def from_int(cls, level: int) -> DifficultyLevel:
        """Parse an integer in [1, 5] into DifficultyLevel or raise InvariantViolationError."""
        require_int_range(level, "difficulty_level", 1, 5, code="INVALID_DIFFICULTY_LEVEL")
        return cls(level)

    def escalate(self) -> DifficultyLevel:
        """Return next higher difficulty level clamped at L5."""
        return DifficultyLevel(min(5, int(self) + 1))

    def deescalate(self) -> DifficultyLevel:
        """Return next lower difficulty level clamped at L1."""
        return DifficultyLevel(max(1, int(self) - 1))


class SessionStatus(StrEnum):
    """Deterministic lifecycle state machine for InterviewSession."""

    SCHEDULED = "SCHEDULED"
    INITIALIZING = "INITIALIZING"
    IN_PROGRESS = "IN_PROGRESS"
    EVALUATING_FINAL = "EVALUATING_FINAL"
    COMPLETED = "COMPLETED"
    TERMINATED_EARLY = "TERMINATED_EARLY"
    ABANDONED = "ABANDONED"


class RoutingAction(StrEnum):
    """Cognitive graph routing actions decided by AdaptiveRoutingDomainService."""

    ESCALATE_DIFFICULTY = "ESCALATE_DIFFICULTY"
    SOCRATIC_DEPTH_PROBE = "SOCRATIC_DEPTH_PROBE"
    SOCRATIC_AUTHENTICITY_PROBE = "SOCRATIC_AUTHENTICITY_PROBE"
    TRANSITION_NEXT_COMPETENCY = "TRANSITION_NEXT_COMPETENCY"
    CONCLUDE_INTERVIEW = "CONCLUDE_INTERVIEW"


@dataclass(frozen=True, slots=True)
class PromptHash(ValueObject):
    """Immutable cryptographic hash and template version of a delivered prompt."""

    sha256: SHA256Hash
    template_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if not isinstance(self.sha256, SHA256Hash):
            raise InvariantViolationError(
                "sha256 must be a SHA256Hash.",
                code="INVALID_PROMPT_HASH",
            )
        cleaned_ver = require_non_empty_str(
            self.template_version,
            "template_version",
            max_len=32,
            code="INVALID_TEMPLATE_VERSION",
        )
        object.__setattr__(self, "template_version", cleaned_ver)

    @classmethod
    def from_prompt_text(cls, prompt_text: str, *, template_version: str = "1.0.0") -> PromptHash:
        """Compute deterministic PromptHash from normalized prompt text."""
        cleaned = require_non_empty_str(
            prompt_text,
            "prompt_text",
            code="EMPTY_PROMPT_TEXT",
        )
        return cls(
            sha256=SHA256Hash.from_text(cleaned),
            template_version=template_version,
        )


@dataclass(frozen=True, slots=True)
class ResponseLatencyMetric(ValueObject):
    """Timing and speech cadence telemetry captured for a single candidate turn."""

    ttft_ms: int
    total_duration_ms: int
    speech_cadence_wpm: float = 0.0
    pause_entropy: float = 0.0

    def __post_init__(self) -> None:
        ttft = require_int_range(self.ttft_ms, "ttft_ms", 0, code="INVALID_LATENCY_METRIC")
        total = require_int_range(
            self.total_duration_ms,
            "total_duration_ms",
            ttft,
            code="INVALID_LATENCY_METRIC",
        )
        wpm = require_float_range(
            self.speech_cadence_wpm,
            "speech_cadence_wpm",
            0.0,
            600.0,
            code="INVALID_CADENCE_METRIC",
        )
        entropy = require_float_range(
            self.pause_entropy,
            "pause_entropy",
            0.0,
            100.0,
            code="INVALID_CADENCE_METRIC",
        )
        object.__setattr__(self, "ttft_ms", ttft)
        object.__setattr__(self, "total_duration_ms", total)
        object.__setattr__(self, "speech_cadence_wpm", wpm)
        object.__setattr__(self, "pause_entropy", entropy)


@dataclass(frozen=True, slots=True)
class TurnEvaluationScore(ValueObject):
    """Four-axis rubric evaluation for a single interview turn."""

    technical_accuracy: float
    depth_of_understanding: float
    tradeoff_awareness: float
    contextual_specificity: float
    suspicion_score: float = 0.0

    def __post_init__(self) -> None:
        acc = require_float_range(
            self.technical_accuracy, "technical_accuracy", 0.0, 5.0, code="INVALID_TURN_SCORE"
        )
        depth = require_float_range(
            self.depth_of_understanding,
            "depth_of_understanding",
            0.0,
            5.0,
            code="INVALID_TURN_SCORE",
        )
        tradeoff = require_float_range(
            self.tradeoff_awareness, "tradeoff_awareness", 0.0, 5.0, code="INVALID_TURN_SCORE"
        )
        spec = require_float_range(
            self.contextual_specificity,
            "contextual_specificity",
            0.0,
            5.0,
            code="INVALID_TURN_SCORE",
        )
        suspicion = require_float_range(
            self.suspicion_score, "suspicion_score", 0.0, 1.0, code="INVALID_SUSPICION_SCORE"
        )
        object.__setattr__(self, "technical_accuracy", acc)
        object.__setattr__(self, "depth_of_understanding", depth)
        object.__setattr__(self, "tradeoff_awareness", tradeoff)
        object.__setattr__(self, "contextual_specificity", spec)
        object.__setattr__(self, "suspicion_score", suspicion)

    def raw_quality_score(self) -> float:
        """Compute normalized weighted turn quality Q_raw in [0.0, 1.0]."""
        weighted = (
            ETA_ACCURACY * self.technical_accuracy
            + ETA_DEPTH * self.depth_of_understanding
            + ETA_TRADEOFF * self.tradeoff_awareness
            + ETA_SPECIFICITY * self.contextual_specificity
        )
        return weighted / 5.0

    def effective_quality_score(self) -> float:
        """Apply critical inaccuracy gatekeeper penalty when accuracy < 1.5."""
        q_raw = self.raw_quality_score()
        if self.technical_accuracy >= 1.5:
            return q_raw
        return q_raw * ((self.technical_accuracy / 1.5) ** 2)


@dataclass(frozen=True, slots=True)
class IntegrityFlag(ValueObject):
    """Domain warning emitted when behavioral or linguistic integrity anomalies fire."""

    flag_code: str
    severity: str
    turn_number: int
    confidence: ConfidenceScore
    evidence_summary: str

    _VALID_SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})

    def __post_init__(self) -> None:
        cleaned_code = require_non_empty_str(
            self.flag_code, "flag_code", max_len=64, code="INVALID_INTEGRITY_FLAG"
        ).upper()
        cleaned_severity = require_non_empty_str(
            self.severity, "severity", max_len=16, code="INVALID_INTEGRITY_SEVERITY"
        ).upper()
        if cleaned_severity not in self._VALID_SEVERITIES:
            raise InvariantViolationError(
                f"Invalid IntegrityFlag severity: {cleaned_severity!r}.",
                code="INVALID_INTEGRITY_SEVERITY",
            )
        require_int_range(self.turn_number, "turn_number", 1, code="INVALID_TURN_NUMBER")
        if not isinstance(self.confidence, ConfidenceScore):
            raise InvariantViolationError(
                "confidence must be a ConfidenceScore.",
                code="INVALID_CONFIDENCE_SCORE",
            )
        cleaned_summary = require_non_empty_str(
            self.evidence_summary,
            "evidence_summary",
            max_len=500,
            code="INVALID_INTEGRITY_FLAG",
        )
        object.__setattr__(self, "flag_code", cleaned_code)
        object.__setattr__(self, "severity", cleaned_severity)
        object.__setattr__(self, "evidence_summary", cleaned_summary)


@dataclass(frozen=True, slots=True)
class RoutingDecision(ValueObject):
    """Deterministic outcome produced by AdaptiveRoutingDomainService."""

    action: RoutingAction
    next_difficulty: DifficultyLevel
    rule_id: str
    rationale: str

    def __post_init__(self) -> None:
        if not isinstance(self.action, RoutingAction):
            raise InvariantViolationError(
                "action must be a RoutingAction.",
                code="INVALID_ROUTING_ACTION",
            )
        if not isinstance(self.next_difficulty, DifficultyLevel):
            raise InvariantViolationError(
                "next_difficulty must be a DifficultyLevel.",
                code="INVALID_DIFFICULTY_LEVEL",
            )
        cleaned_rule = require_non_empty_str(
            self.rule_id, "rule_id", max_len=64, code="INVALID_ROUTING_RULE"
        )
        cleaned_rationale = require_non_empty_str(
            self.rationale, "rationale", max_len=300, code="INVALID_ROUTING_RATIONALE"
        )
        object.__setattr__(self, "rule_id", cleaned_rule)
        object.__setattr__(self, "rationale", cleaned_rationale)
