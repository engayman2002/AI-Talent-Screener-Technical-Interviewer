"""Semantic Matching Engine Entities."""

from __future__ import annotations

from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.matching.value_objects import (
    HybridMatchVector,
)
from talent_screener.domain.shared.base import Entity
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    require_float_range,
    require_non_empty_str,
    require_non_nil_uuid,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7


class CompetencyAlignmentItem(Entity[UUID]):
    """Per-competency semantic match breakdown inside a SemanticMatchResult."""

    __slots__ = (
        "_achieved_score",
        "_canonical_skill_id",
        "_competency_id",
        "_evidence_excerpts",
        "_match_vector",
        "_weight",
    )

    def __init__(
        self,
        *,
        competency_id: UUID,
        canonical_skill_id: str,
        weight: float,
        match_vector: HybridMatchVector,
        achieved_score: float,
        evidence_excerpts: tuple[str, ...] = (),
        item_id: UUID | None = None,
    ) -> None:
        resolved_id = item_id if item_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "item_id", code="INVALID_ALIGNMENT_ITEM_ID")
        require_non_nil_uuid(competency_id, "competency_id", code="INVALID_COMPETENCY_ID")
        super().__init__(resolved_id)

        self._competency_id: UUID = competency_id
        self._canonical_skill_id: str = require_non_empty_str(
            canonical_skill_id,
            "canonical_skill_id",
            max_len=120,
            code="INVALID_SKILL_ID",
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
        if not isinstance(match_vector, HybridMatchVector):
            raise InvariantViolationError(
                "match_vector must be a HybridMatchVector.",
                code="INVALID_MATCH_VECTOR",
            )
        self._match_vector: HybridMatchVector = match_vector
        self._achieved_score: float = require_float_range(
            achieved_score,
            "achieved_score",
            0.0,
            1.0,
            code="INVALID_ACHIEVED_SCORE",
        )
        self._evidence_excerpts: tuple[str, ...] = tuple(
            excerpt.strip() for excerpt in evidence_excerpts if excerpt.strip()
        )

    @property
    def competency_id(self) -> UUID:
        return self._competency_id

    @property
    def canonical_skill_id(self) -> str:
        return self._canonical_skill_id

    @property
    def weight(self) -> float:
        return self._weight

    @property
    def match_vector(self) -> HybridMatchVector:
        return self._match_vector

    @property
    def achieved_score(self) -> float:
        return self._achieved_score

    @property
    def evidence_excerpts(self) -> tuple[str, ...]:
        return self._evidence_excerpts
