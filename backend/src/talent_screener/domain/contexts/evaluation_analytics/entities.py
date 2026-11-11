"""Evaluation & Analytics Bounded Context Entities."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from talent_screener.domain.contexts.evaluation_analytics.exceptions import (
    MissingEvidenceTurnError,
)
from talent_screener.domain.contexts.evaluation_analytics.value_objects import (
    HiringRecommendationBand,
)
from talent_screener.domain.shared.base import Entity
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    require_float_range,
    require_int_range,
    require_non_empty_str,
    require_non_nil_uuid,
    require_utc_datetime,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now


class CompetencyScorecardItem(Entity[UUID]):
    """Per-competency evaluation scorecard entry citing concrete AdaptiveTurn evidence."""

    __slots__ = (
        "_achieved_depth",
        "_competency_id",
        "_competency_name",
        "_evidence_quotes",
        "_evidence_turn_numbers",
        "_normalized_score",
        "_rationale",
        "_target_depth",
        "_weight",
    )

    def __init__(
        self,
        *,
        competency_id: UUID,
        competency_name: str,
        weight: float,
        target_depth: int,
        achieved_depth: float,
        normalized_score: float,
        evidence_turn_numbers: tuple[int, ...],
        evidence_quotes: tuple[str, ...] = (),
        rationale: str = "",
        item_id: UUID | None = None,
    ) -> None:
        resolved_id = item_id if item_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "item_id", code="INVALID_SCORECARD_ITEM_ID")
        require_non_nil_uuid(competency_id, "competency_id", code="INVALID_COMPETENCY_ID")
        super().__init__(resolved_id)

        self._competency_id: UUID = competency_id
        self._competency_name: str = require_non_empty_str(
            competency_name, "competency_name", max_len=120, code="INVALID_COMPETENCY_NAME"
        )
        self._weight: float = require_float_range(
            weight,
            "weight",
            0.0,
            1.0,
            inclusive_min=False,
            inclusive_max=True,
            code="INVALID_COMPETENCY_WEIGHT",
        )
        self._target_depth: int = require_int_range(
            target_depth, "target_depth", 1, 5, code="INVALID_TARGET_DEPTH"
        )
        self._achieved_depth: float = require_float_range(
            achieved_depth, "achieved_depth", 0.0, 5.0, code="INVALID_ACHIEVED_DEPTH"
        )
        self._normalized_score: float = require_float_range(
            normalized_score, "normalized_score", 0.0, 100.0, code="INVALID_SCORECARD_SCORE"
        )

        if not evidence_turn_numbers:
            raise MissingEvidenceTurnError(
                f"CompetencyScorecardItem for {self._competency_name!r} must reference at least "
                "one AdaptiveTurn number (INV-EA-01).",
            )
        validated_turns = tuple(
            require_int_range(turn_num, "evidence_turn_number", 1, code="INVALID_TURN_NUMBER")
            for turn_num in evidence_turn_numbers
        )
        self._evidence_turn_numbers: tuple[int, ...] = validated_turns
        self._evidence_quotes: tuple[str, ...] = tuple(
            q.strip() for q in evidence_quotes if q.strip()
        )
        self._rationale: str = rationale.strip()[:1000]

    @property
    def competency_id(self) -> UUID:
        return self._competency_id

    @property
    def competency_name(self) -> str:
        return self._competency_name

    @property
    def weight(self) -> float:
        return self._weight

    @property
    def target_depth(self) -> int:
        return self._target_depth

    @property
    def achieved_depth(self) -> float:
        return self._achieved_depth

    @property
    def normalized_score(self) -> float:
        return self._normalized_score

    @property
    def evidence_turn_numbers(self) -> tuple[int, ...]:
        return self._evidence_turn_numbers

    @property
    def evidence_quotes(self) -> tuple[str, ...]:
        return self._evidence_quotes

    @property
    def rationale(self) -> str:
        return self._rationale


class IntegrityAuditEntry(Entity[UUID]):
    """Auditable record of a correlated integrity anomaly during an interview."""

    __slots__ = ("_description", "_flag_code", "_severity", "_suspicion_risk", "_turn_number")

    _VALID_SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})

    def __init__(
        self,
        *,
        turn_number: int,
        flag_code: str,
        severity: str,
        suspicion_risk: float,
        description: str,
        entry_id: UUID | None = None,
    ) -> None:
        resolved_id = entry_id if entry_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "entry_id", code="INVALID_AUDIT_ENTRY_ID")
        super().__init__(resolved_id)

        self._turn_number: int = require_int_range(
            turn_number, "turn_number", 1, code="INVALID_TURN_NUMBER"
        )
        self._flag_code: str = require_non_empty_str(
            flag_code, "flag_code", max_len=64, code="INVALID_INTEGRITY_FLAG"
        ).upper()
        cleaned_sev = require_non_empty_str(
            severity, "severity", max_len=16, code="INVALID_INTEGRITY_SEVERITY"
        ).upper()
        if cleaned_sev not in self._VALID_SEVERITIES:
            raise InvariantViolationError(
                f"Invalid IntegrityAuditEntry severity: {cleaned_sev!r}.",
                code="INVALID_INTEGRITY_SEVERITY",
            )
        self._severity: str = cleaned_sev
        self._suspicion_risk: float = require_float_range(
            suspicion_risk, "suspicion_risk", 0.0, 1.0, code="INVALID_SUSPICION_RISK"
        )
        self._description: str = require_non_empty_str(
            description, "description", max_len=500, code="INVALID_AUDIT_DESCRIPTION"
        )

    @property
    def turn_number(self) -> int:
        return self._turn_number

    @property
    def flag_code(self) -> str:
        return self._flag_code

    @property
    def severity(self) -> str:
        return self._severity

    @property
    def suspicion_risk(self) -> float:
        return self._suspicion_risk

    @property
    def description(self) -> str:
        return self._description


class RecruiterOverrideAudit(Entity[UUID]):
    """Append-only human governance override appended to a finalized EvaluationReport."""

    __slots__ = (
        "_actor_id",
        "_justification",
        "_new_band",
        "_previous_band",
        "_recorded_at",
    )

    def __init__(
        self,
        *,
        actor_id: UUID,
        previous_band: HiringRecommendationBand,
        new_band: HiringRecommendationBand,
        justification: str,
        recorded_at: datetime | None = None,
        override_id: UUID | None = None,
    ) -> None:
        resolved_id = override_id if override_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "override_id", code="INVALID_OVERRIDE_ID")
        require_non_nil_uuid(actor_id, "actor_id", code="INVALID_ACTOR_ID")
        super().__init__(resolved_id)

        if not isinstance(previous_band, HiringRecommendationBand) or not isinstance(
            new_band, HiringRecommendationBand
        ):
            raise InvariantViolationError(
                "Override bands must be HiringRecommendationBand enums.",
                code="INVALID_RECOMMENDATION_BAND",
            )
        if previous_band == new_band:
            raise InvariantViolationError(
                "RecruiterOverrideAudit must transition to a distinct recommendation band.",
                code="NOOP_OVERRIDE",
            )
        self._actor_id: UUID = actor_id
        self._previous_band: HiringRecommendationBand = previous_band
        self._new_band: HiringRecommendationBand = new_band
        self._justification: str = require_non_empty_str(
            justification,
            "justification",
            min_len=10,
            max_len=1000,
            code="INVALID_OVERRIDE_JUSTIFICATION",
        )
        self._recorded_at: datetime = (
            require_utc_datetime(recorded_at, "recorded_at", code="INVALID_UTC_DATETIME")
            if recorded_at is not None
            else utc_now()
        )

    @property
    def actor_id(self) -> UUID:
        return self._actor_id

    @property
    def previous_band(self) -> HiringRecommendationBand:
        return self._previous_band

    @property
    def new_band(self) -> HiringRecommendationBand:
        return self._new_band

    @property
    def justification(self) -> str:
        return self._justification

    @property
    def recorded_at(self) -> datetime:
        return self._recorded_at
