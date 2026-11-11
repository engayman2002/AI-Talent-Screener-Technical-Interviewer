"""Semantic Matching Engine Aggregate Root: SemanticMatchResult."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.exceptions import (
    InvalidMatchWeightsError,
)
from talent_screener.domain.contexts.document_intelligence.matching.entities import (
    CompetencyAlignmentItem,
)
from talent_screener.domain.contexts.document_intelligence.matching.events import (
    CandidateMatchedToJobEvent,
    CandidateRejectedByGatekeeperEvent,
    CandidateShortlistedForInterviewEvent,
)
from talent_screener.domain.contexts.document_intelligence.matching.value_objects import (
    CONVEX_WEIGHT_TOLERANCE,
    CandidateTier,
    EpistemicSkillGap,
    HybridMatchVector,
)
from talent_screener.domain.shared.base import AggregateRoot
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import require_non_nil_uuid
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import TenantId, VersionNumber


class SemanticMatchResult(AggregateRoot[UUID]):
    """Deterministic hybrid match evaluation binding a CandidateProfile to a JobRequisition."""

    __slots__ = (
        "_alignment_items",
        "_candidate_profile_id",
        "_failed_gatekeepers",
        "_match_vector",
        "_requisition_id",
        "_rubric_version",
        "_skill_gaps",
        "_tier",
    )

    def __init__(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        rubric_version: VersionNumber,
        match_vector: HybridMatchVector,
        alignment_items: Sequence[CompetencyAlignmentItem],
        skill_gaps: Sequence[EpistemicSkillGap],
        tier: CandidateTier,
        failed_gatekeepers: tuple[str, ...] = (),
        match_result_id: UUID | None = None,
        version: VersionNumber | None = None,
        emit_events: bool = True,
    ) -> None:
        resolved_id = match_result_id if match_result_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "match_result_id", code="INVALID_MATCH_RESULT_ID")
        require_non_nil_uuid(requisition_id, "requisition_id", code="INVALID_REQUISITION_ID")
        require_non_nil_uuid(
            candidate_profile_id, "candidate_profile_id", code="INVALID_PROFILE_ID"
        )
        super().__init__(resolved_id, tenant_id=tenant_id, version=version)

        if not isinstance(rubric_version, VersionNumber):
            raise InvariantViolationError(
                "rubric_version must be a VersionNumber.",
                code="INVALID_VERSION_NUMBER",
            )
        if not isinstance(match_vector, HybridMatchVector):
            raise InvariantViolationError(
                "match_vector must be a HybridMatchVector.",
                code="INVALID_MATCH_VECTOR",
            )
        if not isinstance(tier, CandidateTier):
            raise InvariantViolationError(
                "tier must be a CandidateTier enum.",
                code="INVALID_CANDIDATE_TIER",
            )

        items_tuple = tuple(alignment_items)
        if not items_tuple:
            raise InvariantViolationError(
                "SemanticMatchResult requires at least one CompetencyAlignmentItem.",
                code="EMPTY_ALIGNMENT_ITEMS",
            )
        total_weight = sum(item.weight for item in items_tuple)
        if abs(total_weight - 1.0) > CONVEX_WEIGHT_TOLERANCE:
            raise InvalidMatchWeightsError(
                f"Alignment item weights must sum to 1.0 (+/- {CONVEX_WEIGHT_TOLERANCE}); "
                f"got {total_weight:.9f}.",
            )

        if (
            match_vector.gatekeeper_multiplier == 0.0
            and tier is not CandidateTier.GATEKEEPER_REJECTED
        ):
            raise InvariantViolationError(
                "Gatekeeper failure (G_hard = 0.0) must classify as GATEKEEPER_REJECTED.",
                code="GATEKEEPER_TIER_INCONSISTENCY",
            )
        if match_vector.gatekeeper_multiplier == 0.0 and not failed_gatekeepers:
            raise InvariantViolationError(
                "Gatekeeper rejection must record at least one failed gatekeeper skill.",
                code="MISSING_FAILED_GATEKEEPERS",
            )

        self._requisition_id: UUID = requisition_id
        self._candidate_profile_id: UUID = candidate_profile_id
        self._rubric_version: VersionNumber = rubric_version
        self._match_vector: HybridMatchVector = match_vector
        self._alignment_items: tuple[CompetencyAlignmentItem, ...] = items_tuple
        self._skill_gaps: tuple[EpistemicSkillGap, ...] = tuple(
            sorted(skill_gaps, key=lambda g: g.probe_priority, reverse=True)
        )
        self._tier: CandidateTier = tier
        self._failed_gatekeepers: tuple[str, ...] = tuple(failed_gatekeepers)

        if emit_events:
            score = self._match_vector.composite_score
            self._record_event(
                CandidateMatchedToJobEvent(
                    aggregate_id=self._id,
                    tenant_id=self._tenant_id,
                    match_result_id=self._id,
                    requisition_id=self._requisition_id,
                    candidate_profile_id=self._candidate_profile_id,
                    composite_score=score,
                    assigned_tier=self._tier.value,
                )
            )
            if self._tier in (CandidateTier.FAST_TRACK, CandidateTier.INTERVIEW):
                self._record_event(
                    CandidateShortlistedForInterviewEvent(
                        aggregate_id=self._id,
                        tenant_id=self._tenant_id,
                        match_result_id=self._id,
                        requisition_id=self._requisition_id,
                        candidate_profile_id=self._candidate_profile_id,
                        composite_score=score,
                        tier=self._tier.value,
                        skill_gap_count=len(self._skill_gaps),
                    )
                )
            elif self._tier is CandidateTier.GATEKEEPER_REJECTED:
                self._record_event(
                    CandidateRejectedByGatekeeperEvent(
                        aggregate_id=self._id,
                        tenant_id=self._tenant_id,
                        match_result_id=self._id,
                        requisition_id=self._requisition_id,
                        candidate_profile_id=self._candidate_profile_id,
                        failed_gatekeeper_skills=self._failed_gatekeepers,
                    )
                )

    @property
    def requisition_id(self) -> UUID:
        return self._requisition_id

    @property
    def candidate_profile_id(self) -> UUID:
        return self._candidate_profile_id

    @property
    def rubric_version(self) -> VersionNumber:
        return self._rubric_version

    @property
    def match_vector(self) -> HybridMatchVector:
        return self._match_vector

    @property
    def composite_score(self) -> float:
        return self._match_vector.composite_score

    @property
    def alignment_items(self) -> tuple[CompetencyAlignmentItem, ...]:
        return self._alignment_items

    @property
    def skill_gaps(self) -> tuple[EpistemicSkillGap, ...]:
        return self._skill_gaps

    @property
    def tier(self) -> CandidateTier:
        return self._tier

    @property
    def failed_gatekeepers(self) -> tuple[str, ...]:
        return self._failed_gatekeepers
