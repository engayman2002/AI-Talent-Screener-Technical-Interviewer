"""Recruitment Management Bounded Context Entities."""

from __future__ import annotations

from uuid import UUID

from talent_screener.domain.shared.base import Entity
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    require_finite_float,
    require_float_range,
    require_int_range,
    require_non_empty_str,
    require_non_nil_uuid,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7


class CompetencyCriterion(Entity[UUID]):
    """Child entity defining a technical evaluation criterion within a JobRequisition."""

    __slots__ = (
        "_canonical_skill_id",
        "_ideal_anchor_descriptor",
        "_is_gatekeeper",
        "_name",
        "_raw_importance",
        "_target_depth",
        "_weight",
    )

    def __init__(
        self,
        *,
        name: str,
        canonical_skill_id: str,
        weight: float,
        target_depth: int,
        is_gatekeeper: bool = False,
        raw_importance: float = 1.0,
        ideal_anchor_descriptor: str = "",
        criterion_id: UUID | None = None,
    ) -> None:
        resolved_id = criterion_id if criterion_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "criterion_id", code="INVALID_CRITERION_ID")
        super().__init__(resolved_id)

        self._name: str = require_non_empty_str(
            name,
            "criterion.name",
            min_len=2,
            max_len=120,
            code="INVALID_CRITERION_NAME",
        )
        self._canonical_skill_id: str = require_non_empty_str(
            canonical_skill_id,
            "criterion.canonical_skill_id",
            max_len=120,
            code="INVALID_SKILL_ID",
        )
        self._weight: float = require_float_range(
            weight,
            "criterion.weight",
            0.0,
            1.0,
            inclusive_min=False,
            inclusive_max=True,
            code="INVALID_COMPETENCY_WEIGHT",
        )
        self._target_depth: int = require_int_range(
            target_depth,
            "criterion.target_depth",
            1,
            5,
            code="INVALID_TARGET_DEPTH",
        )
        if not isinstance(is_gatekeeper, bool):
            raise InvariantViolationError(
                "is_gatekeeper must be a boolean.",
                code="INVALID_GATEKEEPER_FLAG",
            )
        self._is_gatekeeper: bool = is_gatekeeper

        importance = require_finite_float(
            raw_importance,
            "criterion.raw_importance",
            code="INVALID_RAW_WEIGHT",
        )
        if importance <= 0.0:
            raise InvariantViolationError(
                "raw_importance must be strictly positive.",
                code="INVALID_RAW_WEIGHT",
            )
        self._raw_importance: float = importance

        cleaned_anchor = ideal_anchor_descriptor.strip()
        if len(cleaned_anchor) > 600:
            cleaned_anchor = require_non_empty_str(
                cleaned_anchor,
                "criterion.ideal_anchor_descriptor",
                max_len=600,
                code="INVALID_ANCHOR_DESCRIPTOR",
            )
        self._ideal_anchor_descriptor: str = cleaned_anchor

    @property
    def name(self) -> str:
        return self._name

    @property
    def canonical_skill_id(self) -> str:
        return self._canonical_skill_id

    @property
    def weight(self) -> float:
        return self._weight

    @property
    def target_depth(self) -> int:
        return self._target_depth

    @property
    def is_gatekeeper(self) -> bool:
        return self._is_gatekeeper

    @property
    def raw_importance(self) -> float:
        return self._raw_importance

    @property
    def ideal_anchor_descriptor(self) -> str:
        return self._ideal_anchor_descriptor

    def assign_normalized_weight(self, weight: float) -> None:
        """Internal package helper called by JobRequisition during weight calibration."""
        self._weight = require_float_range(
            weight,
            "criterion.weight",
            0.0,
            1.0,
            inclusive_min=False,
            inclusive_max=True,
            code="INVALID_COMPETENCY_WEIGHT",
        )
