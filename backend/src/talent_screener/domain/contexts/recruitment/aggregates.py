"""Recruitment Management Bounded Context Aggregate Root: JobRequisition."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from talent_screener.domain.contexts.recruitment.entities import CompetencyCriterion
from talent_screener.domain.contexts.recruitment.events import (
    JobRequisitionArchivedEvent,
    JobRequisitionPublishedEvent,
    JobRequisitionRubricUpdatedEvent,
)
from talent_screener.domain.contexts.recruitment.exceptions import (
    GatekeeperRequiredError,
    RubricLockedError,
)
from talent_screener.domain.contexts.recruitment.value_objects import (
    CompetencyWeightVector,
    HardSkillGatekeeperSet,
    RequisitionStatus,
    SeniorityLevel,
    TierThresholdConfig,
)
from talent_screener.domain.shared.base import AggregateRoot
from talent_screener.domain.shared.exceptions import (
    InvalidStateTransitionError,
    InvariantViolationError,
)
from talent_screener.domain.shared.guards import (
    require_finite_float,
    require_int_range,
    require_non_empty_str,
    require_non_nil_uuid,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import TenantId, VersionNumber

MAX_COMPETENCY_CRITERIA: int = 12

_ALLOWED_REQUISITION_TRANSITIONS: dict[RequisitionStatus, frozenset[RequisitionStatus]] = {
    RequisitionStatus.DRAFT: frozenset({RequisitionStatus.PUBLISHED, RequisitionStatus.ARCHIVED}),
    RequisitionStatus.PUBLISHED: frozenset({RequisitionStatus.PAUSED, RequisitionStatus.ARCHIVED}),
    RequisitionStatus.PAUSED: frozenset({RequisitionStatus.PUBLISHED, RequisitionStatus.ARCHIVED}),
    RequisitionStatus.ARCHIVED: frozenset(),
}


class JobRequisition(AggregateRoot[UUID]):
    """Authoritative hiring mandate owning weighted competency rubric and lifecycle state."""

    __slots__ = (
        "_active_interview_session_ids",
        "_active_screening_batch_ids",
        "_criteria",
        "_description",
        "_gatekeepers",
        "_has_completed_interviews",
        "_interview_timebox_minutes",
        "_rubric_version",
        "_rubric_version_id",
        "_seniority_level",
        "_status",
        "_tier_thresholds",
        "_title",
        "_weight_vector",
    )

    def __init__(
        self,
        *,
        tenant_id: TenantId,
        title: str,
        description: str,
        seniority_level: SeniorityLevel,
        criteria: Sequence[CompetencyCriterion],
        gatekeepers: HardSkillGatekeeperSet | None = None,
        tier_thresholds: TierThresholdConfig | None = None,
        interview_timebox_minutes: int = 30,
        requisition_id: UUID | None = None,
        version: VersionNumber | None = None,
    ) -> None:
        resolved_id = requisition_id if requisition_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "requisition_id", code="INVALID_REQUISITION_ID")
        super().__init__(resolved_id, tenant_id=tenant_id, version=version)

        self._title: str = require_non_empty_str(
            title,
            "title",
            min_len=5,
            max_len=120,
            code="INVALID_REQUISITION_TITLE",
        )
        self._description: str = require_non_empty_str(
            description,
            "description",
            min_len=10,
            code="INVALID_REQUISITION_DESCRIPTION",
        )
        if not isinstance(seniority_level, SeniorityLevel):
            raise InvariantViolationError(
                "seniority_level must be a SeniorityLevel enum.",
                code="INVALID_SENIORITY_LEVEL",
            )
        self._seniority_level: SeniorityLevel = seniority_level
        self._status: RequisitionStatus = RequisitionStatus.DRAFT
        self._interview_timebox_minutes: int = require_int_range(
            interview_timebox_minutes,
            "interview_timebox_minutes",
            15,
            60,
            code="INVALID_INTERVIEW_TIMEBOX",
        )

        self._tier_thresholds: TierThresholdConfig = (
            tier_thresholds if tier_thresholds is not None else TierThresholdConfig()
        )
        self._criteria: list[CompetencyCriterion] = []
        self._weight_vector: CompetencyWeightVector
        self._gatekeepers: HardSkillGatekeeperSet
        self._apply_rubric_configuration(criteria, gatekeepers)

        self._rubric_version: VersionNumber = VersionNumber(1)
        self._rubric_version_id: UUID = generate_uuid7()
        self._active_screening_batch_ids: set[UUID] = set()
        self._active_interview_session_ids: set[UUID] = set()
        self._has_completed_interviews: bool = False

    def _apply_rubric_configuration(
        self,
        criteria: Sequence[CompetencyCriterion],
        gatekeepers: HardSkillGatekeeperSet | None,
    ) -> None:
        criteria_list = list(criteria)
        if not criteria_list or len(criteria_list) > MAX_COMPETENCY_CRITERIA:
            raise InvariantViolationError(
                f"JobRequisition requires between 1 and {MAX_COMPETENCY_CRITERIA} criteria; "
                f"got {len(criteria_list)}.",
                code="INVALID_CRITERIA_COUNT",
            )
        seen_ids = {item.id for item in criteria_list}
        if len(seen_ids) != len(criteria_list):
            raise InvariantViolationError(
                "Duplicate CompetencyCriterion IDs are not permitted.",
                code="DUPLICATE_CRITERION_ID",
            )
        weight_vector = CompetencyWeightVector.from_mapping(
            {str(item.id): item.weight for item in criteria_list}
        )
        resolved_gatekeepers = (
            gatekeepers
            if gatekeepers is not None
            else HardSkillGatekeeperSet.from_skill_ids(
                item.canonical_skill_id for item in criteria_list if item.is_gatekeeper
            )
        )
        self._criteria = criteria_list
        self._weight_vector = weight_vector
        self._gatekeepers = resolved_gatekeepers

    @property
    def title(self) -> str:
        return self._title

    @property
    def description(self) -> str:
        return self._description

    @property
    def seniority_level(self) -> SeniorityLevel:
        return self._seniority_level

    @property
    def status(self) -> RequisitionStatus:
        return self._status

    @property
    def criteria(self) -> tuple[CompetencyCriterion, ...]:
        return tuple(self._criteria)

    @property
    def weight_vector(self) -> CompetencyWeightVector:
        return self._weight_vector

    @property
    def gatekeepers(self) -> HardSkillGatekeeperSet:
        return self._gatekeepers

    @property
    def tier_thresholds(self) -> TierThresholdConfig:
        return self._tier_thresholds

    @property
    def interview_timebox_minutes(self) -> int:
        return self._interview_timebox_minutes

    @property
    def rubric_version(self) -> VersionNumber:
        return self._rubric_version

    @property
    def rubric_version_id(self) -> UUID:
        return self._rubric_version_id

    @property
    def active_screening_batch_ids(self) -> frozenset[UUID]:
        return frozenset(self._active_screening_batch_ids)

    @property
    def active_interview_session_ids(self) -> frozenset[UUID]:
        return frozenset(self._active_interview_session_ids)

    @property
    def is_rubric_locked(self) -> bool:
        """True if an active ScreeningBatch or active/completed InterviewSession locks edits."""
        return bool(
            self._active_screening_batch_ids
            or self._active_interview_session_ids
            or self._has_completed_interviews
        )

    def _transition_status(self, target: RequisitionStatus) -> RequisitionStatus:
        allowed = _ALLOWED_REQUISITION_TRANSITIONS[self._status]
        if target not in allowed:
            raise InvalidStateTransitionError(
                f"Cannot transition JobRequisition from {self._status} to {target}.",
                from_state=self._status.value,
                to_state=target.value,
                code="INVALID_REQUISITION_TRANSITION",
            )
        previous = self._status
        self._status = target
        self._increment_version()
        return previous

    def publish(self) -> None:
        """Validate rubric gatekeepers & weights and transition requisition to PUBLISHED."""
        has_gatekeeper_criterion = any(item.is_gatekeeper for item in self._criteria)
        if not has_gatekeeper_criterion and self._gatekeepers.is_empty:
            raise GatekeeperRequiredError(
                "Published JobRequisition must designate at least one Must-Have gatekeeper.",
            )
        self._transition_status(RequisitionStatus.PUBLISHED)
        self._record_event(
            JobRequisitionPublishedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                requisition_id=self._id,
                title=self._title,
                seniority_level=self._seniority_level.value,
                rubric_version=int(self._rubric_version),
                rubric_version_id=self._rubric_version_id,
            )
        )

    def pause(self) -> None:
        """Pause a published requisition."""
        self._transition_status(RequisitionStatus.PAUSED)

    def archive(self) -> None:
        """Permanently archive the requisition."""
        previous = self._transition_status(RequisitionStatus.ARCHIVED)
        self._record_event(
            JobRequisitionArchivedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                requisition_id=self._id,
                previous_status=previous.value,
            )
        )

    def _ensure_rubric_mutable(self) -> None:
        if self._status is RequisitionStatus.ARCHIVED:
            raise InvalidStateTransitionError(
                "Cannot modify rubric of an archived JobRequisition.",
                from_state=self._status.value,
                code="REQUISITION_ARCHIVED",
            )
        if self.is_rubric_locked:
            raise RubricLockedError(
                "Rubric cannot be mutated while an active ScreeningBatch or InterviewSession "
                "references this JobRequisition.",
            )

    def update_rubric(
        self,
        *,
        criteria: Sequence[CompetencyCriterion],
        gatekeepers: HardSkillGatekeeperSet | None = None,
        tier_thresholds: TierThresholdConfig | None = None,
    ) -> None:
        """Update competency criteria, weights, and thresholds when rubric is unlocked."""
        self._ensure_rubric_mutable()
        self._apply_rubric_configuration(criteria, gatekeepers)
        if tier_thresholds is not None:
            self._tier_thresholds = tier_thresholds
        self._rubric_version = self._rubric_version.next()
        self._rubric_version_id = generate_uuid7()
        self._increment_version()
        self._record_event(
            JobRequisitionRubricUpdatedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                requisition_id=self._id,
                rubric_version=int(self._rubric_version),
                rubric_version_id=self._rubric_version_id,
                competency_count=len(self._criteria),
            )
        )

    @staticmethod
    def calculate_dynamic_weights(
        criteria: Sequence[CompetencyCriterion],
        *,
        gatekeeper_multiplier: float = 1.0,
    ) -> CompetencyWeightVector:
        """Compute normalized competency weights with optional gatekeeper importance boost."""
        boost = require_finite_float(
            gatekeeper_multiplier,
            "gatekeeper_multiplier",
            code="INVALID_GATEKEEPER_MULTIPLIER",
        )
        if boost <= 0.0:
            raise InvariantViolationError(
                "gatekeeper_multiplier must be strictly positive.",
                code="INVALID_GATEKEEPER_MULTIPLIER",
            )
        raw_scores: dict[str, float] = {}
        for item in criteria:
            factor = boost if item.is_gatekeeper else 1.0
            raw_scores[str(item.id)] = item.raw_importance * factor
        return CompetencyWeightVector.normalize_from_raw(raw_scores)

    def rebalance_weights_dynamically(
        self,
        *,
        gatekeeper_multiplier: float = 1.0,
    ) -> CompetencyWeightVector:
        """Recalculate and persist normalized competency weights across current criteria."""
        self._ensure_rubric_mutable()
        new_vector = self.calculate_dynamic_weights(
            self._criteria,
            gatekeeper_multiplier=gatekeeper_multiplier,
        )
        mapping = new_vector.as_mapping()
        for item in self._criteria:
            item.assign_normalized_weight(mapping[str(item.id)])
        self._weight_vector = new_vector
        self._rubric_version = self._rubric_version.next()
        self._rubric_version_id = generate_uuid7()
        self._increment_version()
        self._record_event(
            JobRequisitionRubricUpdatedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                requisition_id=self._id,
                rubric_version=int(self._rubric_version),
                rubric_version_id=self._rubric_version_id,
                competency_count=len(self._criteria),
            )
        )
        return new_vector

    def register_active_screening_batch(self, batch_id: UUID) -> None:
        """Lock rubric updates while a ScreeningBatch is actively running."""
        require_non_nil_uuid(batch_id, "batch_id", code="INVALID_BATCH_ID")
        self._active_screening_batch_ids.add(batch_id)

    def release_screening_batch(self, batch_id: UUID) -> None:
        """Release a completed ScreeningBatch lock."""
        self._active_screening_batch_ids.discard(batch_id)

    def register_active_interview_session(self, session_id: UUID) -> None:
        """Lock rubric updates while an InterviewSession is active."""
        require_non_nil_uuid(session_id, "session_id", code="INVALID_SESSION_ID")
        self._active_interview_session_ids.add(session_id)

    def complete_interview_session(self, session_id: UUID) -> None:
        """Record interview completion, preserving rubric immutability (INV-RM-02)."""
        self._active_interview_session_ids.discard(session_id)
        self._has_completed_interviews = True
