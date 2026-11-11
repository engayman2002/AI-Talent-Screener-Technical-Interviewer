"""Pure Domain Service implementing hybrid semantic matching and tier classification."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.aggregates import CandidateProfile
from talent_screener.domain.contexts.document_intelligence.exceptions import (
    InvalidMatchWeightsError,
)
from talent_screener.domain.contexts.document_intelligence.matching.aggregates import (
    SemanticMatchResult,
)
from talent_screener.domain.contexts.document_intelligence.matching.entities import (
    CompetencyAlignmentItem,
)
from talent_screener.domain.contexts.document_intelligence.matching.value_objects import (
    CONVEX_WEIGHT_TOLERANCE,
    CandidateTier,
    ChunkEvidenceSample,
    CompetencyEvaluationSpec,
    EpistemicSkillGap,
    GapType,
    HybridMatchVector,
    MatchThresholds,
    RecencyDecayFactor,
)
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import clamp
from talent_screener.domain.shared.value_objects import TenantId, VersionNumber

_TOP_P_WEIGHTS: tuple[float, ...] = (0.65, 0.25, 0.10)


class HybridMatchingDomainService:
    """Stateless domain service computing deterministic hybrid match scores & skill gaps."""

    def evaluate_gatekeepers(
        self,
        candidate_profile: CandidateProfile,
        competencies: Sequence[CompetencyEvaluationSpec],
    ) -> tuple[float, tuple[str, ...]]:
        """Evaluate strict hard-skill gatekeepers, returning G_hard in {0.0, 1.0}."""
        applied_by_skill: dict[str, int] = {}
        for claim in candidate_profile.skill_claims:
            current = applied_by_skill.get(claim.canonical_skill_id, 0)
            applied_by_skill[claim.canonical_skill_id] = max(current, claim.applied_months)

        failed_skills: list[str] = []
        for spec in competencies:
            if not spec.is_gatekeeper or not spec.strict_gatekeeper:
                continue
            verified_months = applied_by_skill.get(spec.canonical_skill_id)
            if verified_months is None or verified_months < spec.min_applied_months:
                failed_skills.append(spec.canonical_skill_id)

        if failed_skills:
            return 0.0, tuple(failed_skills)
        return 1.0, ()

    def evaluate_competency(
        self,
        *,
        candidate_profile: CandidateProfile,
        spec: CompetencyEvaluationSpec,
        chunks: Sequence[ChunkEvidenceSample],
        alpha_dense: float = 0.55,
        beta_sparse: float = 0.25,
        gamma_recency: float = 0.20,
        recency_decay: RecencyDecayFactor | None = None,
    ) -> tuple[CompetencyAlignmentItem, EpistemicSkillGap | None]:
        """Compute Top-P pooled hybrid alignment and epistemic skill gap for one competency."""
        decay_model = recency_decay if recency_decay is not None else RecencyDecayFactor()
        matching_claims = [
            claim
            for claim in candidate_profile.skill_claims
            if claim.canonical_skill_id == spec.canonical_skill_id
        ]

        if chunks:
            scored_chunks: list[tuple[float, ChunkEvidenceSample]] = []
            for chunk in chunks:
                depth_scale = 0.25 + 0.25 * chunk.evidentiary_depth
                exp_align = clamp(depth_scale / 1.25, 0.0, 1.0)
                vec = HybridMatchVector(
                    dense_similarity=chunk.dense_similarity,
                    sparse_bm25_score=chunk.sparse_bm25_score,
                    experience_alignment=exp_align,
                    alpha_dense=alpha_dense,
                    beta_sparse=beta_sparse,
                    gamma_recency=gamma_recency,
                    gatekeeper_multiplier=1.0,
                    recency_decay=decay_model,
                    recency_months_elapsed=chunk.recency_months_delta,
                )
                scored_chunks.append((vec.raw_convex_score, chunk))

            scored_chunks.sort(key=lambda pair: pair[0], reverse=True)
            top_chunks = scored_chunks[: len(_TOP_P_WEIGHTS)]
            weight_slice = _TOP_P_WEIGHTS[: len(top_chunks)]
            weight_norm = sum(weight_slice)

            pooled_dense = sum(
                (w / weight_norm) * item[1].dense_similarity
                for w, item in zip(weight_slice, top_chunks, strict=True)
            )
            pooled_sparse = sum(
                (w / weight_norm) * item[1].sparse_bm25_score
                for w, item in zip(weight_slice, top_chunks, strict=True)
            )
            pooled_exp = sum(
                (w / weight_norm)
                * clamp((0.25 + 0.25 * item[1].evidentiary_depth) / 1.25, 0.0, 1.0)
                for w, item in zip(weight_slice, top_chunks, strict=True)
            )
            best_recency_months = min(item[1].recency_months_delta for item in top_chunks)
            excerpts = tuple(
                item[1].verbatim_excerpt for item in top_chunks if item[1].verbatim_excerpt
            )
        else:
            pooled_dense = 0.0
            pooled_sparse = 0.0
            pooled_exp = 0.0
            best_recency_months = (
                min(c.recency_months for c in matching_claims) if matching_claims else 0
            )
            excerpts = tuple(c.source_quote for c in matching_claims[:3])

        comp_vector = HybridMatchVector(
            dense_similarity=clamp(pooled_dense, 0.0, 1.0),
            sparse_bm25_score=clamp(pooled_sparse, 0.0, 1.0),
            experience_alignment=clamp(pooled_exp, 0.0, 1.0),
            alpha_dense=alpha_dense,
            beta_sparse=beta_sparse,
            gamma_recency=gamma_recency,
            gatekeeper_multiplier=1.0,
            recency_decay=decay_model,
            recency_months_elapsed=best_recency_months,
        )
        achieved = comp_vector.raw_convex_score
        alignment_item = CompetencyAlignmentItem(
            competency_id=spec.competency_id,
            canonical_skill_id=spec.canonical_skill_id,
            weight=spec.weight,
            match_vector=comp_vector,
            achieved_score=achieved,
            evidence_excerpts=excerpts,
        )

        # Compute Epistemic Skill Gap: Pi_k = w_k * (0.60 * (1 - C_k) + 0.40 * I_vague)
        is_vague = not matching_claims or max(c.claimed_depth for c in matching_claims) <= 2
        gap_indicator = 1.0 if is_vague else 0.0
        probe_priority = clamp(
            spec.weight * (0.60 * (1.0 - achieved) + 0.40 * gap_indicator), 0.0, 1.0
        )

        gap: EpistemicSkillGap | None = None
        if achieved < 0.85 or is_vague:
            if not matching_claims and achieved < 0.25:
                gap_type = GapType.MISSING_EVIDENCE
                summary = f"No direct CV evidence found for {spec.canonical_skill_id}."
            elif best_recency_months > 36:
                gap_type = GapType.STALE_EXPERIENCE
                summary = f"Evidence for {spec.canonical_skill_id} is > 36 months old."
            elif is_vague:
                gap_type = GapType.UNVERIFIED_CLAIM
                summary = f"Skill {spec.canonical_skill_id} lacks deep production ownership proof."
            else:
                gap_type = GapType.BELOW_REQUIRED_DEPTH
                summary = f"Demonstrated depth for {spec.canonical_skill_id} is below target level."
            gap = EpistemicSkillGap(
                competency_id=spec.competency_id,
                canonical_skill_id=spec.canonical_skill_id,
                gap_type=gap_type,
                probe_priority=probe_priority,
                suspect_claim_summary=summary,
            )

        return alignment_item, gap

    def classify_tier(
        self,
        *,
        composite_score: float,
        gatekeeper_multiplier: float,
        thresholds: MatchThresholds,
    ) -> CandidateTier:
        """Deterministically classify candidate into screening tier."""
        if gatekeeper_multiplier == 0.0:
            return CandidateTier.GATEKEEPER_REJECTED
        if composite_score >= thresholds.fast_track_threshold:
            return CandidateTier.FAST_TRACK
        if composite_score >= thresholds.interview_threshold:
            return CandidateTier.INTERVIEW
        if composite_score >= thresholds.review_threshold:
            return CandidateTier.MANUAL_REVIEW
        return CandidateTier.REJECTED

    def score_candidate(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        rubric_version: VersionNumber,
        candidate_profile: CandidateProfile,
        competencies: Sequence[CompetencyEvaluationSpec],
        evidence_by_competency: Mapping[UUID, Sequence[ChunkEvidenceSample]],
        thresholds: MatchThresholds | None = None,
        alpha_dense: float = 0.55,
        beta_sparse: float = 0.25,
        gamma_recency: float = 0.20,
        recency_decay: RecencyDecayFactor | None = None,
    ) -> SemanticMatchResult:
        """Execute full hybrid semantic evaluation and emit SemanticMatchResult aggregate."""
        if not competencies:
            raise InvariantViolationError(
                "At least one CompetencyEvaluationSpec is required for matching.",
                code="EMPTY_COMPETENCIES",
            )
        weight_sum = sum(c.weight for c in competencies)
        if abs(weight_sum - 1.0) > CONVEX_WEIGHT_TOLERANCE:
            raise InvalidMatchWeightsError(
                f"Competency weights must sum to 1.0 (+/- {CONVEX_WEIGHT_TOLERANCE}); "
                f"got {weight_sum:.9f}.",
            )

        decay_model = recency_decay if recency_decay is not None else RecencyDecayFactor()
        resolved_thresholds = thresholds if thresholds is not None else MatchThresholds()

        gatekeeper_multiplier, failed_gatekeepers = self.evaluate_gatekeepers(
            candidate_profile,
            competencies,
        )

        alignment_items: list[CompetencyAlignmentItem] = []
        skill_gaps: list[EpistemicSkillGap] = []
        agg_dense = 0.0
        agg_sparse = 0.0
        agg_recency_exp = 0.0

        for spec in competencies:
            chunks = evidence_by_competency.get(spec.competency_id, ())
            item, gap = self.evaluate_competency(
                candidate_profile=candidate_profile,
                spec=spec,
                chunks=chunks,
                alpha_dense=alpha_dense,
                beta_sparse=beta_sparse,
                gamma_recency=gamma_recency,
                recency_decay=decay_model,
            )
            alignment_items.append(item)
            if gap is not None:
                skill_gaps.append(gap)
            agg_dense += spec.weight * item.match_vector.dense_similarity
            agg_sparse += spec.weight * item.match_vector.sparse_bm25_score
            agg_recency_exp += spec.weight * item.match_vector.recency_adjusted_experience

        aggregate_vector = HybridMatchVector(
            dense_similarity=clamp(agg_dense, 0.0, 1.0),
            sparse_bm25_score=clamp(agg_sparse, 0.0, 1.0),
            experience_alignment=clamp(agg_recency_exp, 0.0, 1.0),
            alpha_dense=alpha_dense,
            beta_sparse=beta_sparse,
            gamma_recency=gamma_recency,
            gatekeeper_multiplier=gatekeeper_multiplier,
            recency_decay=decay_model,
            recency_months_elapsed=0,
        )

        tier = self.classify_tier(
            composite_score=aggregate_vector.composite_score,
            gatekeeper_multiplier=gatekeeper_multiplier,
            thresholds=resolved_thresholds,
        )

        return SemanticMatchResult(
            tenant_id=tenant_id,
            requisition_id=requisition_id,
            candidate_profile_id=candidate_profile.id,
            rubric_version=rubric_version,
            match_vector=aggregate_vector,
            alignment_items=alignment_items,
            skill_gaps=skill_gaps,
            tier=tier,
            failed_gatekeepers=failed_gatekeepers,
        )
