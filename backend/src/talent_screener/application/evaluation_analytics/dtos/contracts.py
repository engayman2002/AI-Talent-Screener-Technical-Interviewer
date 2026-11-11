"""Immutable Request, Response, and Matrix DTOs for Evaluation & Analytics."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from talent_screener.domain.contexts.evaluation_analytics.value_objects import (
    HiringRecommendationBand,
)
from talent_screener.domain.shared.value_objects import TenantId


@dataclass(frozen=True, slots=True)
class GenerateEvaluationReportRequestDTO:
    """Command DTO to synthesize and finalize a post-interview EvaluationReport."""

    tenant_id: TenantId
    interview_session_id: UUID
    executive_summary: str = ""


@dataclass(frozen=True, slots=True)
class ScorecardItemReadDTO:
    """Immutable read projection of one CompetencyScorecardItem."""

    competency_id: UUID
    competency_name: str
    weight: float
    target_depth: int
    achieved_depth: float
    normalized_score: float
    evidence_turn_numbers: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class EvaluationReportResponseDTO:
    """Immutable dossier DTO returned after generation or override."""

    report_id: UUID
    interview_session_id: UUID
    requisition_id: UUID
    candidate_profile_id: UUID
    technical_interview_score: float
    cv_match_score: float
    authenticity_score: float
    recommendation_index: float
    initial_recommendation_band: HiringRecommendationBand
    effective_recommendation_band: HiringRecommendationBand
    is_finalized: bool
    report_hash: str | None
    scorecard_items: tuple[ScorecardItemReadDTO, ...]
    override_count: int


@dataclass(frozen=True, slots=True)
class RecordRecruiterOverrideRequestDTO:
    """Command DTO appending a human disposition override to a finalized report."""

    tenant_id: TenantId
    report_id: UUID
    actor_id: UUID
    new_band: HiringRecommendationBand
    justification: str


@dataclass(frozen=True, slots=True)
class BuildCandidateComparisonMatrixRequestDTO:
    """Query/Command DTO comparing 2 to 4 finalists side-by-side on a JobRequisition."""

    tenant_id: TenantId
    requisition_id: UUID
    candidate_profile_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class ComparisonMatrixCellDTO:
    """Normalized per-candidate, per-competency cell in the comparison matrix."""

    candidate_profile_id: UUID
    competency_id: UUID
    competency_name: str
    cv_prior_score: float
    live_interview_score: float
    posterior_score: float
    verification_delta: float
    verification_classification: str
    achieved_depth: float
    target_depth: int
    is_dimension_leader: bool


@dataclass(frozen=True, slots=True)
class CandidateComparisonSummaryDTO:
    """Normalized column summary for a ranked candidate in the comparison matrix."""

    candidate_profile_id: UUID
    cv_match_score: float
    posterior_technical_score: float
    holistic_decision_index: float
    authenticity_score: float
    recommendation_band: HiringRecommendationBand
    rank: int


@dataclass(frozen=True, slots=True)
class CandidateComparisonMatrixDto:
    """Complete Side-by-Side Candidate Comparison Matrix projection."""

    requisition_id: UUID
    candidates: tuple[CandidateComparisonSummaryDTO, ...]
    dimension_cells: tuple[ComparisonMatrixCellDTO, ...]
