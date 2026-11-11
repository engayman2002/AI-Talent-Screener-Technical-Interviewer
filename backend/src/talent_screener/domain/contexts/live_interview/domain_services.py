"""Pure Domain Service for adaptive interview difficulty calibration and branching."""

from __future__ import annotations

from collections.abc import Sequence

from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    RoutingAction,
    RoutingDecision,
    TurnEvaluationScore,
)
from talent_screener.domain.shared.guards import (
    require_float_range,
    require_int_range,
)

MAX_SOCRATIC_PROBES: int = 2
MAX_PRIMARY_QUESTIONS_PER_COMPETENCY: int = 2
HINT_DISCOUNT_XI: float = 0.08


class AdaptiveRoutingDomainService:
    """Deterministic mathematical policy engine for LangGraph interview routing."""

    def calibrate_next_difficulty(
        self,
        current_level: DifficultyLevel,
        evaluation: TurnEvaluationScore,
    ) -> DifficultyLevel:
        """Calibrate next difficulty level L_k^(t+1) in {1..5} from Q_eff and depth."""
        q_eff = evaluation.effective_quality_score()
        if q_eff >= 0.78 and evaluation.depth_of_understanding >= 3.8:
            return current_level.escalate()
        if q_eff >= 0.45:
            return current_level
        return current_level.deescalate()

    def compute_demonstrated_depth(
        self,
        *,
        difficulty: DifficultyLevel,
        effective_quality: float,
        probe_index: int = 0,
    ) -> float:
        """Compute D_dem^(j) = L_k^(j) * Q_eff^(j) * (1 - 0.08 * f_j)."""
        q_eff = require_float_range(
            effective_quality, "effective_quality", 0.0, 1.0, code="INVALID_QUALITY_SCORE"
        )
        require_int_range(
            probe_index, "probe_index", 0, MAX_SOCRATIC_PROBES, code="INVALID_PROBE_INDEX"
        )
        return float(int(difficulty)) * q_eff * (1.0 - HINT_DISCOUNT_XI * probe_index)

    def compute_competency_mastery(
        self,
        turn_observations: Sequence[tuple[DifficultyLevel, float, int]],
    ) -> float:
        """Compute M_k = 0.70 * max(D_dem) + 0.30 * sum(L * D_dem) / sum(L)."""
        if not turn_observations:
            return 0.0
        demonstrated_depths: list[float] = []
        weighted_sum = 0.0
        level_sum = 0.0
        for level, q_eff, probe_idx in turn_observations:
            d_dem = self.compute_demonstrated_depth(
                difficulty=level,
                effective_quality=q_eff,
                probe_index=probe_idx,
            )
            demonstrated_depths.append(d_dem)
            lvl = float(int(level))
            weighted_sum += lvl * d_dem
            level_sum += lvl

        peak = max(demonstrated_depths)
        consistency = weighted_sum / level_sum if level_sum > 0.0 else 0.0
        return 0.70 * peak + 0.30 * consistency

    def decide_next_route(
        self,
        *,
        evaluation: TurnEvaluationScore,
        current_difficulty: DifficultyLevel,
        current_mastery: float,
        target_mastery: float,
        active_probe_count: int,
        primary_questions_asked: int,
        completed_turns: int,
        max_turns: int,
        elapsed_seconds: float,
        max_wall_clock_seconds: float,
        tokens_consumed: int,
        token_budget_limit: int,
        remaining_competencies_count: int,
        turn_suspicion_risk: float = 0.0,
        rolling_authenticity_index: float = 1.0,
        critical_abort: bool = False,
    ) -> RoutingDecision:
        """Evaluate ordered piecewise routing function R(S^(t)) (P1..P6)."""
        q_eff = evaluation.effective_quality_score()

        # P1: Global execution budget or critical security abort
        if (
            completed_turns >= max_turns
            or elapsed_seconds >= 0.92 * max_wall_clock_seconds
            or tokens_consumed >= token_budget_limit
            or critical_abort
        ):
            return RoutingDecision(
                action=RoutingAction.CONCLUDE_INTERVIEW,
                next_difficulty=current_difficulty,
                rule_id="ROUTE_GLOBAL_BUDGET_EXHAUSTED",
                rationale="Session turn, wall-clock, token budget, or security ceiling reached.",
            )

        # P2: Authenticity Socratic Challenge
        if (
            turn_suspicion_risk >= 0.60 or rolling_authenticity_index < 0.65
        ) and active_probe_count < MAX_SOCRATIC_PROBES:
            return RoutingDecision(
                action=RoutingAction.SOCRATIC_AUTHENTICITY_PROBE,
                next_difficulty=current_difficulty,
                rule_id="ROUTE_AUTHENTICITY_SOCRATIC_CHALLENGE",
                rationale="Suspicion risk or rolling authenticity crossed verification threshold.",
            )

        # P3: Socratic Depth Probe
        if (
            (0.35 <= q_eff < 0.72) or evaluation.contextual_specificity < 2.5
        ) and active_probe_count < MAX_SOCRATIC_PROBES:
            return RoutingDecision(
                action=RoutingAction.SOCRATIC_DEPTH_PROBE,
                next_difficulty=current_difficulty,
                rule_id="ROUTE_SOCRATIC_DEPTH_PROBE",
                rationale="Surface or unspecific response requires targeted Socratic follow-up.",
            )

        # P4: Competency Difficulty Escalation
        if (
            q_eff >= 0.72
            and current_mastery < target_mastery
            and primary_questions_asked < MAX_PRIMARY_QUESTIONS_PER_COMPETENCY
        ):
            next_diff = self.calibrate_next_difficulty(current_difficulty, evaluation)
            return RoutingDecision(
                action=RoutingAction.ESCALATE_DIFFICULTY,
                next_difficulty=next_diff,
                rule_id="ROUTE_COMPETENCY_DIFFICULTY_ESCALATION",
                rationale="Strong answer warrants escalated difficulty within active competency.",
            )

        # P5: Advance to Next Competency
        if (
            current_mastery >= target_mastery
            or active_probe_count >= MAX_SOCRATIC_PROBES
            or q_eff < 0.35
            or primary_questions_asked >= MAX_PRIMARY_QUESTIONS_PER_COMPETENCY
        ) and remaining_competencies_count > 0:
            return RoutingDecision(
                action=RoutingAction.TRANSITION_NEXT_COMPETENCY,
                next_difficulty=current_difficulty,
                rule_id="ROUTE_ADVANCE_NEXT_COMPETENCY",
                rationale=(
                    "Competency target met or probes exhausted; advancing to next competency."
                ),
            )

        # P6: All Competencies Complete
        return RoutingDecision(
            action=RoutingAction.CONCLUDE_INTERVIEW,
            next_difficulty=current_difficulty,
            rule_id="ROUTE_ALL_COMPETENCIES_COMPLETE",
            rationale="All queued competencies evaluated within session bounds.",
        )
