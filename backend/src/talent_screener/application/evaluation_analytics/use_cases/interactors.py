"""Use Case interactors for Evaluation & Analytics Bounded Context."""

from __future__ import annotations

from uuid import UUID

from talent_screener.application.common.exceptions import (
    ResourceConflictError,
    ResourceNotFoundError,
)
from talent_screener.application.common.ports.outbound import TransactionalUnitOfWorkPort
from talent_screener.application.evaluation_analytics.dtos import (
    BuildCandidateComparisonMatrixRequestDTO,
    CandidateComparisonMatrixDto,
    CandidateComparisonSummaryDTO,
    ComparisonMatrixCellDTO,
    EvaluationReportResponseDTO,
    GenerateEvaluationReportRequestDTO,
    RecordRecruiterOverrideRequestDTO,
    ScorecardItemReadDTO,
)
from talent_screener.domain.contexts.document_intelligence import (
    SemanticMatchResultRepositoryPort,
)
from talent_screener.domain.contexts.evaluation_analytics import (
    AuthenticityIndex,
    CompetencyScorecardItem,
    CompositeRubricScore,
    EvaluationReport,
    EvaluationReportRepositoryPort,
    HiringRecommendationBand,
    IntegrityAuditEntry,
    RecruiterOverrideAudit,
)
from talent_screener.domain.contexts.live_interview import (
    AdaptiveRoutingDomainService,
    InterviewSessionRepositoryPort,
)
from talent_screener.domain.contexts.recruitment import JobRequisitionRepositoryPort
from talent_screener.domain.shared.guards import clamp


def _to_report_dto(report: EvaluationReport) -> EvaluationReportResponseDTO:
    return EvaluationReportResponseDTO(
        report_id=report.id,
        interview_session_id=report.interview_session_id,
        requisition_id=report.requisition_id,
        candidate_profile_id=report.candidate_profile_id,
        technical_interview_score=report.composite_score.technical_interview_score,
        cv_match_score=report.composite_score.cv_match_score,
        authenticity_score=report.authenticity_index.score,
        recommendation_index=report.composite_score.recommendation_index,
        initial_recommendation_band=report.initial_recommendation_band,
        effective_recommendation_band=report.effective_recommendation_band,
        is_finalized=report.is_finalized,
        report_hash=report.report_hash.value if report.report_hash is not None else None,
        scorecard_items=tuple(
            ScorecardItemReadDTO(
                competency_id=item.competency_id,
                competency_name=item.competency_name,
                weight=item.weight,
                target_depth=item.target_depth,
                achieved_depth=item.achieved_depth,
                normalized_score=item.normalized_score,
                evidence_turn_numbers=item.evidence_turn_numbers,
            )
            for item in report.scorecard_items
        ),
        override_count=len(report.override_audits),
    )


def _select_recommendation_band(recommendation_index: float) -> HiringRecommendationBand:
    if recommendation_index >= 85.0:
        return HiringRecommendationBand.STRONG_HIRE
    if recommendation_index >= 72.0:
        return HiringRecommendationBand.HIRE
    if recommendation_index >= 58.0:
        return HiringRecommendationBand.BORDERLINE_HOLD
    return HiringRecommendationBand.NO_HIRE


class GenerateEvaluationReportUseCase:
    """Synthesizes InterviewSession turns, match scores, and integrity into an immutable report."""

    def __init__(
        self,
        *,
        session_repo: InterviewSessionRepositoryPort,
        requisition_repo: JobRequisitionRepositoryPort,
        match_repo: SemanticMatchResultRepositoryPort,
        report_repo: EvaluationReportRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._session_repo = session_repo
        self._requisition_repo = requisition_repo
        self._match_repo = match_repo
        self._report_repo = report_repo
        self._uow = uow
        self._routing_service = AdaptiveRoutingDomainService()

    async def execute(
        self, request: GenerateEvaluationReportRequestDTO
    ) -> EvaluationReportResponseDTO:
        existing = self._report_repo.get_by_session_id(
            request.tenant_id, request.interview_session_id
        )
        if existing is not None:
            return _to_report_dto(existing)

        session = self._session_repo.get_by_id(request.tenant_id, request.interview_session_id)
        if session is None:
            raise ResourceNotFoundError(
                f"InterviewSession {request.interview_session_id} not found.",
                code="SESSION_NOT_FOUND",
            )
        if not session.turns:
            raise ResourceConflictError(
                "Cannot generate EvaluationReport for an InterviewSession with zero turns.",
                code="NO_TURNS_TO_EVALUATE",
            )

        requisition = self._requisition_repo.get_by_id(request.tenant_id, session.requisition_id)
        if requisition is None:
            raise ResourceNotFoundError(
                f"JobRequisition {session.requisition_id} not found.",
                code="REQUISITION_NOT_FOUND",
            )

        match_result = self._match_repo.get_by_candidate_and_requisition(
            request.tenant_id, session.requisition_id, session.candidate_profile_id
        )
        cv_match_score = match_result.composite_score if match_result is not None else 0.0

        telemetry_by_turn = {snap.turn_number: snap for snap in session.telemetry_snapshots}
        auth_index = AuthenticityIndex(score=1.0)
        audit_entries: list[IntegrityAuditEntry] = []

        for turn in session.turns:
            snap = telemetry_by_turn.get(turn.turn_number)
            p_client = (
                AuthenticityIndex.compute_client_penalty(
                    blur_count=snap.focus_loss_count,
                    blur_duration_seconds=snap.focus_loss_duration_ms / 1000.0,
                    external_paste=snap.external_paste_detected,
                    keystroke_anomaly=snap.keystroke_anomaly_detected,
                )
                if snap is not None
                else 0.0
            )
            p_cadence = (
                0.70
                if (
                    turn.latency_metric is not None
                    and turn.latency_metric.ttft_ms > 8500
                    and turn.latency_metric.speech_cadence_wpm > 165.0
                )
                else 0.0
            )
            r_turn = AuthenticityIndex.compute_turn_suspicion_risk(
                p_client=p_client,
                p_cadence=p_cadence,
                p_stylo=turn.evaluation.suspicion_score,
            )
            socratic_verified = (
                turn.socratic_probe is not None
                and turn.evaluation.effective_quality_score() >= 0.72
            )
            auth_index = auth_index.apply_turn_update(
                turn_suspicion_risk=r_turn,
                socratic_verified=socratic_verified,
            )
            for flag in turn.integrity_flags:
                audit_entries.append(
                    IntegrityAuditEntry(
                        turn_number=flag.turn_number,
                        flag_code=flag.flag_code,
                        severity=flag.severity,
                        suspicion_risk=r_turn,
                        description=flag.evidence_summary,
                    )
                )

        fallback_turn = session.turns[-1].turn_number
        scorecard_items: list[CompetencyScorecardItem] = []
        for criterion in requisition.criteria:
            comp_turns = [t for t in session.turns if t.competency_id == criterion.id]
            if comp_turns:
                observations = [
                    (
                        t.difficulty,
                        t.evaluation.effective_quality_score(),
                        t.socratic_probe.probe_index if t.socratic_probe is not None else 0,
                    )
                    for t in comp_turns
                ]
                achieved_depth = clamp(
                    self._routing_service.compute_competency_mastery(observations),
                    0.0,
                    5.0,
                )
                attainment = min(1.0, achieved_depth / float(criterion.target_depth))
                norm_score = clamp(attainment * 100.0, 0.0, 100.0)
                turn_nums = tuple(t.turn_number for t in comp_turns)
                quotes = tuple(t.candidate_response[:200] for t in comp_turns)
                rationale = (
                    f"Evaluated across {len(comp_turns)} turn(s) reaching depth "
                    f"{achieved_depth:.2f}/{criterion.target_depth}."
                )
            else:
                achieved_depth = 0.0
                norm_score = 0.0
                turn_nums = (fallback_turn,)
                quotes = ()
                rationale = "Unexamined within session budget; baseline score recorded."

            scorecard_items.append(
                CompetencyScorecardItem(
                    competency_id=criterion.id,
                    competency_name=criterion.name,
                    weight=criterion.weight,
                    target_depth=criterion.target_depth,
                    achieved_depth=achieved_depth,
                    normalized_score=norm_score,
                    evidence_turn_numbers=turn_nums,
                    evidence_quotes=quotes,
                    rationale=rationale,
                )
            )

        technical_score = sum(item.weight * item.normalized_score for item in scorecard_items)
        composite_score = CompositeRubricScore.from_components(
            technical_interview_score=technical_score,
            cv_match_score=cv_match_score,
            authenticity_index=auth_index,
        )
        band = _select_recommendation_band(composite_score.recommendation_index)
        summary = (
            request.executive_summary.strip()
            or f"Automated synthesis across {len(session.turns)} turns: "
            f"Technical={technical_score:.1f}, Authenticity={auth_index.score:.2f}."
        )

        report = EvaluationReport(
            tenant_id=request.tenant_id,
            interview_session_id=session.id,
            requisition_id=requisition.id,
            candidate_profile_id=session.candidate_profile_id,
            scorecard_items=scorecard_items,
            composite_score=composite_score,
            authenticity_index=auth_index,
            recommendation_band=band,
            executive_summary=summary,
            integrity_entries=audit_entries,
            valid_turn_numbers=frozenset(t.turn_number for t in session.turns),
        )
        report.finalize()

        async with self._uow:
            await self._uow.begin(request.tenant_id)
            self._report_repo.save(report)
            self._uow.register_aggregate(report)
            await self._uow.commit()

        return _to_report_dto(report)


class RecordRecruiterOverrideUseCase:
    """Appends an immutable human governance override onto a finalized EvaluationReport."""

    def __init__(
        self,
        *,
        report_repo: EvaluationReportRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._report_repo = report_repo
        self._uow = uow

    async def execute(
        self, request: RecordRecruiterOverrideRequestDTO
    ) -> EvaluationReportResponseDTO:
        async with self._uow:
            await self._uow.begin(request.tenant_id)
            report = self._report_repo.get_by_id(request.tenant_id, request.report_id)
            if report is None:
                raise ResourceNotFoundError(
                    f"EvaluationReport {request.report_id} not found.",
                    code="REPORT_NOT_FOUND",
                )
            override = RecruiterOverrideAudit(
                actor_id=request.actor_id,
                previous_band=report.effective_recommendation_band,
                new_band=request.new_band,
                justification=request.justification,
            )
            report.record_recruiter_override(override)
            self._report_repo.save(report)
            self._uow.register_aggregate(report)
            await self._uow.commit()

        return _to_report_dto(report)


class BuildCandidateComparisonMatrixUseCase:
    """Normalizes 2 to 4 evaluated finalists across rubric axes and authenticity."""

    def __init__(
        self,
        *,
        requisition_repo: JobRequisitionRepositoryPort,
        report_repo: EvaluationReportRepositoryPort,
        match_repo: SemanticMatchResultRepositoryPort,
    ) -> None:
        self._requisition_repo = requisition_repo
        self._report_repo = report_repo
        self._match_repo = match_repo

    async def execute(
        self, request: BuildCandidateComparisonMatrixRequestDTO
    ) -> CandidateComparisonMatrixDto:
        candidate_ids = request.candidate_profile_ids
        if len(candidate_ids) < 2 or len(candidate_ids) > 4:
            raise ResourceConflictError(
                "Candidate comparison matrix requires between 2 and 4 candidates.",
                code="INVALID_COMPARISON_CARDINALITY",
            )
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ResourceConflictError(
                "Duplicate candidate_profile_ids provided in comparison matrix.",
                code="DUPLICATE_COMPARISON_CANDIDATE",
            )

        requisition = self._requisition_repo.get_by_id(request.tenant_id, request.requisition_id)
        if requisition is None:
            raise ResourceNotFoundError(
                f"JobRequisition {request.requisition_id} not found.",
                code="REQUISITION_NOT_FOUND",
            )

        reports_by_cand: dict[UUID, EvaluationReport] = {}
        cv_scores_by_cand: dict[UUID, dict[UUID, float]] = {}
        overall_cv_by_cand: dict[UUID, float] = {}

        for cand_id in candidate_ids:
            report = self._report_repo.get_by_candidate_and_requisition(
                request.tenant_id, request.requisition_id, cand_id
            )
            if report is None:
                raise ResourceNotFoundError(
                    f"EvaluationReport missing for candidate {cand_id}.",
                    code="REPORT_NOT_FOUND",
                )
            reports_by_cand[cand_id] = report

            match_res = self._match_repo.get_by_candidate_and_requisition(
                request.tenant_id, request.requisition_id, cand_id
            )
            if match_res is not None:
                cv_scores_by_cand[cand_id] = {
                    item.competency_id: item.achieved_score for item in match_res.alignment_items
                }
                overall_cv_by_cand[cand_id] = match_res.composite_score
            else:
                cv_scores_by_cand[cand_id] = {}
                overall_cv_by_cand[cand_id] = report.composite_score.cv_match_score

        # Phase 7 Section 3.2 Bayesian Posterior & Verification Delta math
        tau_0 = 0.50
        theta_safe = 0.85
        beta_int = 1.5
        raw_cells: list[tuple[UUID, UUID, str, float, float, float, float, str, float, int]] = []
        posterior_totals: dict[UUID, float] = dict.fromkeys(candidate_ids, 0.0)
        best_score_by_comp: dict[UUID, float] = {}

        for criterion in requisition.criteria:
            best_for_dim = -1.0
            for cand_id in candidate_ids:
                report = reports_by_cand[cand_id]
                item_matches = [
                    it for it in report.scorecard_items if it.competency_id == criterion.id
                ]
                item = item_matches[0] if item_matches else None
                cv_prior = cv_scores_by_cand[cand_id].get(criterion.id, 0.0)
                if item is not None and item.achieved_depth > 0.0:
                    live_norm = clamp(item.achieved_depth / 5.0, 0.0, 1.0)
                    n_turns = len(item.evidence_turn_numbers)
                    rho = (n_turns * 0.90) / (tau_0 + n_turns * 0.90)
                    posterior = 100.0 * ((1.0 - rho) * cv_prior + rho * live_norm)
                    delta = live_norm - cv_prior
                    if delta >= 0.15:
                        classification = "HIDDEN_GEM_UPGRADE"
                    elif delta <= -0.25:
                        classification = "RESUME_INFLATION_WARNING"
                    else:
                        classification = "CORROBORATED_CLAIM"
                    achieved = item.achieved_depth
                else:
                    live_norm = 0.0
                    posterior = 100.0 * cv_prior
                    delta = -cv_prior
                    classification = "CV_PRIOR_ONLY"
                    achieved = 0.0

                posterior_totals[cand_id] += criterion.weight * posterior
                if achieved >= float(criterion.target_depth) and posterior > best_for_dim:
                    best_for_dim = posterior
                raw_cells.append(
                    (
                        cand_id,
                        criterion.id,
                        criterion.name,
                        cv_prior * 100.0,
                        live_norm * 100.0,
                        posterior,
                        delta,
                        classification,
                        achieved,
                        criterion.target_depth,
                    )
                )
            best_score_by_comp[criterion.id] = best_for_dim

        cells = tuple(
            ComparisonMatrixCellDTO(
                candidate_profile_id=cid,
                competency_id=comp_id,
                competency_name=comp_name,
                cv_prior_score=cv_pct,
                live_interview_score=live_pct,
                posterior_score=post,
                verification_delta=delta,
                verification_classification=classification,
                achieved_depth=achieved,
                target_depth=target,
                is_dimension_leader=(
                    best_score_by_comp[comp_id] >= 0.0
                    and abs(post - best_score_by_comp[comp_id]) < 1e-6
                    and achieved >= float(target)
                ),
            )
            for (
                cid,
                comp_id,
                comp_name,
                cv_pct,
                live_pct,
                post,
                delta,
                classification,
                achieved,
                target,
            ) in raw_cells
        )

        unsorted_summaries: list[
            tuple[float, UUID, float, float, float, HiringRecommendationBand]
        ] = []
        for cand_id in candidate_ids:
            report = reports_by_cand[cand_id]
            post_tech = clamp(posterior_totals[cand_id], 0.0, 100.0)
            auth = report.authenticity_index.score
            moderator = min(1.0, (auth / theta_safe) ** beta_int) if auth > 0.0 else 0.0
            holistic = clamp(post_tech * moderator, 0.0, 100.0)
            unsorted_summaries.append(
                (
                    holistic,
                    cand_id,
                    overall_cv_by_cand[cand_id],
                    post_tech,
                    auth,
                    report.effective_recommendation_band,
                )
            )

        unsorted_summaries.sort(key=lambda row: row[0], reverse=True)
        candidates = tuple(
            CandidateComparisonSummaryDTO(
                candidate_profile_id=cand_id,
                cv_match_score=cv_score,
                posterior_technical_score=post_tech,
                holistic_decision_index=holistic,
                authenticity_score=auth,
                recommendation_band=band,
                rank=idx + 1,
            )
            for idx, (holistic, cand_id, cv_score, post_tech, auth, band) in enumerate(
                unsorted_summaries
            )
        )

        return CandidateComparisonMatrixDto(
            requisition_id=request.requisition_id,
            candidates=candidates,
            dimension_cells=cells,
        )
