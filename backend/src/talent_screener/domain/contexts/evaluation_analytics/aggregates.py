"""Evaluation & Analytics Bounded Context Aggregate Root: EvaluationReport."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from talent_screener.domain.contexts.evaluation_analytics.entities import (
    CompetencyScorecardItem,
    IntegrityAuditEntry,
    RecruiterOverrideAudit,
)
from talent_screener.domain.contexts.evaluation_analytics.events import (
    EvaluationReportGeneratedEvent,
    HighRiskIntegrityAlertRaisedEvent,
    RecruiterDecisionRecordedEvent,
)
from talent_screener.domain.contexts.evaluation_analytics.exceptions import (
    CompositeScoreMismatchError,
    MissingEvidenceTurnError,
    ReportImmutableError,
)
from talent_screener.domain.contexts.evaluation_analytics.value_objects import (
    AuthenticityIndex,
    CompositeRubricScore,
    HiringRecommendationBand,
)
from talent_screener.domain.shared.base import AggregateRoot
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import require_non_empty_str, require_non_nil_uuid
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import SHA256Hash, TenantId, VersionNumber

SCORECARD_WEIGHT_TOLERANCE: float = 1e-6
COMPOSITE_ALIGNMENT_TOLERANCE: float = 0.05


class EvaluationReport(AggregateRoot[UUID]):
    """Immutable-once-finalized evaluation dossier supporting append-only recruiter overrides."""

    __slots__ = (
        "_authenticity_index",
        "_candidate_profile_id",
        "_composite_score",
        "_effective_band",
        "_executive_summary",
        "_initial_band",
        "_integrity_entries",
        "_interview_session_id",
        "_is_finalized",
        "_override_audits",
        "_report_hash",
        "_requisition_id",
        "_scorecard_items",
        "_valid_turn_numbers",
    )

    def __init__(
        self,
        *,
        tenant_id: TenantId,
        interview_session_id: UUID,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        scorecard_items: Sequence[CompetencyScorecardItem],
        composite_score: CompositeRubricScore,
        authenticity_index: AuthenticityIndex,
        recommendation_band: HiringRecommendationBand,
        executive_summary: str,
        integrity_entries: Sequence[IntegrityAuditEntry] = (),
        valid_turn_numbers: frozenset[int] | None = None,
        report_id: UUID | None = None,
        version: VersionNumber | None = None,
    ) -> None:
        resolved_id = report_id if report_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "report_id", code="INVALID_REPORT_ID")
        require_non_nil_uuid(
            interview_session_id, "interview_session_id", code="INVALID_SESSION_ID"
        )
        require_non_nil_uuid(requisition_id, "requisition_id", code="INVALID_REQUISITION_ID")
        require_non_nil_uuid(
            candidate_profile_id, "candidate_profile_id", code="INVALID_PROFILE_ID"
        )
        super().__init__(resolved_id, tenant_id=tenant_id, version=version)

        if not isinstance(authenticity_index, AuthenticityIndex):
            raise InvariantViolationError(
                "authenticity_index must be an AuthenticityIndex.",
                code="INVALID_AUTHENTICITY_INDEX",
            )
        if not isinstance(composite_score, CompositeRubricScore):
            raise InvariantViolationError(
                "composite_score must be a CompositeRubricScore.",
                code="INVALID_COMPOSITE_SCORE",
            )
        if not isinstance(recommendation_band, HiringRecommendationBand):
            raise InvariantViolationError(
                "recommendation_band must be a HiringRecommendationBand.",
                code="INVALID_RECOMMENDATION_BAND",
            )

        self._interview_session_id: UUID = interview_session_id
        self._requisition_id: UUID = requisition_id
        self._candidate_profile_id: UUID = candidate_profile_id
        self._valid_turn_numbers: frozenset[int] | None = valid_turn_numbers
        self._scorecard_items: tuple[CompetencyScorecardItem, ...] = ()
        self._composite_score: CompositeRubricScore = composite_score
        self._authenticity_index: AuthenticityIndex = authenticity_index
        self._initial_band: HiringRecommendationBand = recommendation_band
        self._effective_band: HiringRecommendationBand = recommendation_band
        self._executive_summary: str = require_non_empty_str(
            executive_summary,
            "executive_summary",
            min_len=10,
            max_len=4000,
            code="INVALID_EXECUTIVE_SUMMARY",
        )
        self._integrity_entries: tuple[IntegrityAuditEntry, ...] = tuple(integrity_entries)
        self._override_audits: list[RecruiterOverrideAudit] = []
        self._is_finalized: bool = False
        self._report_hash: SHA256Hash | None = None

        self._validate_and_assign_scores(scorecard_items, composite_score)

    def _validate_and_assign_scores(
        self,
        scorecard_items: Sequence[CompetencyScorecardItem],
        composite_score: CompositeRubricScore,
    ) -> None:
        items_tuple = tuple(scorecard_items)
        if not items_tuple:
            raise InvariantViolationError(
                "EvaluationReport requires at least one CompetencyScorecardItem.",
                code="EMPTY_SCORECARD",
            )
        weight_sum = sum(item.weight for item in items_tuple)
        if abs(weight_sum - 1.0) > SCORECARD_WEIGHT_TOLERANCE:
            raise InvariantViolationError(
                f"Scorecard competency weights must sum to 1.0 (+/- {SCORECARD_WEIGHT_TOLERANCE}); "
                f"got {weight_sum:.9f}.",
                code="INVALID_SCORECARD_WEIGHTS",
            )
        for item in items_tuple:
            if self._valid_turn_numbers is not None:
                missing = [
                    turn_no
                    for turn_no in item.evidence_turn_numbers
                    if turn_no not in self._valid_turn_numbers
                ]
                if missing:
                    raise MissingEvidenceTurnError(
                        f"Scorecard item {item.competency_name!r} references nonexistent "
                        f"turn numbers {missing}.",
                    )

        expected_tech = sum(item.weight * item.normalized_score for item in items_tuple)
        if abs(expected_tech - composite_score.technical_interview_score) > (
            COMPOSITE_ALIGNMENT_TOLERANCE
        ):
            raise CompositeScoreMismatchError(
                f"Composite technical score ({composite_score.technical_interview_score:.4f}) "
                f"does not align with weighted scorecard sum ({expected_tech:.4f}).",
            )
        self._scorecard_items = items_tuple
        self._composite_score = composite_score

    @property
    def interview_session_id(self) -> UUID:
        return self._interview_session_id

    @property
    def requisition_id(self) -> UUID:
        return self._requisition_id

    @property
    def candidate_profile_id(self) -> UUID:
        return self._candidate_profile_id

    @property
    def scorecard_items(self) -> tuple[CompetencyScorecardItem, ...]:
        return self._scorecard_items

    @property
    def composite_score(self) -> CompositeRubricScore:
        return self._composite_score

    @property
    def authenticity_index(self) -> AuthenticityIndex:
        return self._authenticity_index

    @property
    def initial_recommendation_band(self) -> HiringRecommendationBand:
        return self._initial_band

    @property
    def effective_recommendation_band(self) -> HiringRecommendationBand:
        return self._effective_band

    @property
    def executive_summary(self) -> str:
        return self._executive_summary

    @property
    def integrity_entries(self) -> tuple[IntegrityAuditEntry, ...]:
        return self._integrity_entries

    @property
    def override_audits(self) -> tuple[RecruiterOverrideAudit, ...]:
        return tuple(self._override_audits)

    @property
    def is_finalized(self) -> bool:
        return self._is_finalized

    @property
    def report_hash(self) -> SHA256Hash | None:
        return self._report_hash

    def update_scorecard(
        self,
        *,
        scorecard_items: Sequence[CompetencyScorecardItem],
        composite_score: CompositeRubricScore,
        executive_summary: str | None = None,
    ) -> None:
        """Update draft scorecard prior to finalization; forbidden once finalized (INV-EA-03)."""
        if self._is_finalized:
            raise ReportImmutableError(
                "Finalized EvaluationReport is cryptographically immutable; use "
                "record_recruiter_override() to record human decisions.",
            )
        self._validate_and_assign_scores(scorecard_items, composite_score)
        if executive_summary is not None:
            self._executive_summary = require_non_empty_str(
                executive_summary,
                "executive_summary",
                min_len=10,
                max_len=4000,
                code="INVALID_EXECUTIVE_SUMMARY",
            )
        self._increment_version()

    def _compute_canonical_hash(self) -> SHA256Hash:
        items_sig = "|".join(
            f"{item.competency_id}:{item.normalized_score:.4f}:{item.evidence_turn_numbers}"
            for item in self._scorecard_items
        )
        payload = (
            f"{self._id}|{self._tenant_id}|{self._interview_session_id}|"
            f"{self._composite_score.technical_interview_score:.4f}|"
            f"{self._authenticity_index.score:.4f}|{self._initial_band.value}|{items_sig}"
        )
        return SHA256Hash.from_text(payload)

    def finalize(self) -> SHA256Hash:
        """Seal report immutably with SHA-256 integrity hash and emit domain events."""
        if self._is_finalized:
            raise ReportImmutableError("EvaluationReport has already been finalized.")
        self._report_hash = self._compute_canonical_hash()
        self._is_finalized = True
        self._increment_version()

        self._record_event(
            EvaluationReportGeneratedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                report_id=self._id,
                interview_session_id=self._interview_session_id,
                candidate_profile_id=self._candidate_profile_id,
                requisition_id=self._requisition_id,
                technical_score=self._composite_score.technical_interview_score,
                authenticity_score=self._authenticity_index.score,
                recommendation_band=self._initial_band.value,
                report_hash=self._report_hash.value,
            )
        )

        high_sev_flags = tuple(
            entry.flag_code
            for entry in self._integrity_entries
            if entry.severity in ("HIGH", "CRITICAL")
        )
        if self._authenticity_index.score < 0.60 or high_sev_flags:
            self._record_event(
                HighRiskIntegrityAlertRaisedEvent(
                    aggregate_id=self._id,
                    tenant_id=self._tenant_id,
                    report_id=self._id,
                    interview_session_id=self._interview_session_id,
                    authenticity_score=self._authenticity_index.score,
                    flag_codes=high_sev_flags,
                )
            )
        return self._report_hash

    def record_recruiter_override(self, override: RecruiterOverrideAudit) -> None:
        """Append an immutable RecruiterOverrideAudit after report finalization."""
        if not self._is_finalized:
            raise InvariantViolationError(
                "Recruiter overrides can only be appended after EvaluationReport is finalized.",
                code="REPORT_NOT_FINALIZED",
            )
        if not isinstance(override, RecruiterOverrideAudit):
            raise InvariantViolationError(
                "override must be a RecruiterOverrideAudit entity.",
                code="INVALID_OVERRIDE_AUDIT",
            )
        if override.previous_band != self._effective_band:
            raise InvariantViolationError(
                f"Override previous_band ({override.previous_band.value}) must match current "
                f"effective band ({self._effective_band.value}).",
                code="OVERRIDE_BAND_MISMATCH",
            )
        self._override_audits.append(override)
        self._effective_band = override.new_band
        self._increment_version()
        self._record_event(
            RecruiterDecisionRecordedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                report_id=self._id,
                actor_id=override.actor_id,
                previous_band=override.previous_band.value,
                new_band=override.new_band.value,
                justification=override.justification,
            )
        )
