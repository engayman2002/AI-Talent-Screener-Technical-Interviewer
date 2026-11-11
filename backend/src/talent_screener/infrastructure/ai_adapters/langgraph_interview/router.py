"""Deterministic Conditional Edge Router delegating P1..P6 rules to Domain Services."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from talent_screener.application.live_interview.dtos import CognitiveGraphContextDTO
from talent_screener.domain.contexts.evaluation_analytics.value_objects import (
    AuthenticityIndex,
)
from talent_screener.domain.contexts.live_interview.domain_services import (
    AdaptiveRoutingDomainService,
)
from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    RoutingAction,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview.nodes import (
    CognitiveEvaluationPayload,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview.state import (
    AdaptiveRoutingPlane,
    InterviewGraphPhase,
    InterviewGraphState,
)


@dataclass(frozen=True, slots=True)
class ConditionalRouteOutcome:
    """Resolved edge routing decision plus updated integrity and epistemic mastery state."""

    action: RoutingAction
    next_difficulty: DifficultyLevel
    next_competency_id: UUID
    rule_id: str
    rationale: str
    updated_mastery_by_competency: MappingProxyType[UUID, float]
    p_client: float
    p_cadence: float
    p_stylo: float
    turn_suspicion_risk: float
    updated_authenticity_index: float
    next_questions_asked: int
    next_probes_used: int
    next_phase: InterviewGraphPhase


class AdaptiveEdgeRouter:
    """Routes between QuestionGenerator, SocraticFollowUp, NextCompetency, and Verification."""

    def __init__(self, routing_service: AdaptiveRoutingDomainService | None = None) -> None:
        self._routing_service = (
            routing_service if routing_service is not None else AdaptiveRoutingDomainService()
        )

    def evaluate_route(
        self,
        *,
        state: InterviewGraphState,
        context: CognitiveGraphContextDTO,
        eval_payload: CognitiveEvaluationPayload,
        projected_total_tokens: int,
    ) -> ConditionalRouteOutcome:
        """Compute integrity update, competency mastery M_k, and piecewise route R(S^(t))."""
        active_comp = state.rubric.competency_by_id(context.active_competency.competency_id)
        active_comp_id = active_comp.competency_id
        current_difficulty = context.current_difficulty

        # Use corroborated telemetry signals per ADR-005 & Phase 4 Eq. 3.2
        telemetry = context.turn_telemetry_signals
        p_client = AuthenticityIndex.compute_client_penalty(
            blur_count=telemetry.focus_loss_count,
            blur_duration_seconds=telemetry.focus_loss_duration_ms / 1000.0,
            external_paste=telemetry.external_paste_detected,
            keystroke_anomaly=telemetry.keystroke_anomaly_detected,
        )
        p_cadence = (
            0.70
            if telemetry.response_latency_ms > 8500 and telemetry.speech_cadence_wpm > 165.0
            else 0.0
        )
        p_stylo = eval_payload.stylometric_suspicion
        r_turn = AuthenticityIndex.compute_turn_suspicion_risk(
            p_client=p_client,
            p_cadence=p_cadence,
            p_stylo=p_stylo,
        )

        # Grant Socratic recovery only when answering an active probe with high genuine quality
        is_answering_probe = state.routing.phase is InterviewGraphPhase.SOCRATIC_PROBING
        socratic_verified = (
            is_answering_probe
            and eval_payload.turn_evaluation.effective_quality_score() >= 0.72
            and p_client < 0.20
        )
        current_auth = AuthenticityIndex(score=state.routing.rolling_authenticity_index)
        next_auth = current_auth.apply_turn_update(
            turn_suspicion_risk=r_turn,
            socratic_verified=socratic_verified,
        )

        # Compute mastery observations consistently with ProcessCandidateTurnUseCase
        prior_obs = list(state.observations_for(active_comp_id))
        prior_obs.append(
            (
                current_difficulty,
                eval_payload.turn_evaluation.effective_quality_score(),
                0,
            )
        )
        updated_mastery = self._routing_service.compute_competency_mastery(prior_obs)
        mastery_map = dict(state.routing.mastery_by_competency)
        mastery_map[active_comp_id] = updated_mastery

        active_probes = max(
            state.routing.probes_used_in_active_competency,
            context.socratic_followup_count,
        )
        questions_so_far = max(
            state.routing.questions_asked_in_active_competency,
            context.primary_questions_asked,
        )
        next_primary_count = questions_so_far if is_answering_probe else questions_so_far + 1

        elapsed_seconds = float(
            max(0, state.metadata.max_wall_clock_seconds - context.remaining_seconds)
        )
        remaining_comps = state.rubric.remaining_count_after(active_comp_id)

        decision = self._routing_service.decide_next_route(
            evaluation=eval_payload.turn_evaluation,
            current_difficulty=current_difficulty,
            current_mastery=updated_mastery,
            target_mastery=float(active_comp.target_depth),
            active_probe_count=active_probes,
            primary_questions_asked=next_primary_count,
            completed_turns=context.turn_sequence_number,
            max_turns=state.metadata.max_turns,
            elapsed_seconds=elapsed_seconds,
            max_wall_clock_seconds=float(state.metadata.max_wall_clock_seconds),
            tokens_consumed=projected_total_tokens,
            token_budget_limit=state.metadata.token_budget_limit,
            remaining_competencies_count=remaining_comps,
            turn_suspicion_risk=r_turn,
            rolling_authenticity_index=next_auth.score,
        )

        next_comp_id = active_comp_id
        next_difficulty = decision.next_difficulty
        next_probes = active_probes
        next_questions = next_primary_count
        next_phase = InterviewGraphPhase.QUESTION_GENERATED

        if decision.action in (
            RoutingAction.SOCRATIC_DEPTH_PROBE,
            RoutingAction.SOCRATIC_AUTHENTICITY_PROBE,
        ):
            next_probes = active_probes + 1
            next_phase = InterviewGraphPhase.SOCRATIC_PROBING
        elif decision.action is RoutingAction.TRANSITION_NEXT_COMPETENCY:
            next_target = state.rubric.next_competency_after(active_comp_id)
            if next_target is not None:
                next_comp_id = next_target.competency_id
                # Reset per-competency counters on competency boundary transition
                next_questions = 0
                next_probes = 0
                baseline = min(4, max(2, next_target.target_depth - 1))
                next_difficulty = DifficultyLevel.from_int(baseline)
        elif decision.action is RoutingAction.CONCLUDE_INTERVIEW:
            next_phase = InterviewGraphPhase.COMPLETED

        return ConditionalRouteOutcome(
            action=decision.action,
            next_difficulty=next_difficulty,
            next_competency_id=next_comp_id,
            rule_id=decision.rule_id,
            rationale=decision.rationale,
            updated_mastery_by_competency=MappingProxyType(mastery_map),
            p_client=p_client,
            p_cadence=p_cadence,
            p_stylo=p_stylo,
            turn_suspicion_risk=r_turn,
            updated_authenticity_index=next_auth.score,
            next_questions_asked=next_questions,
            next_probes_used=next_probes,
            next_phase=next_phase,
        )

    @staticmethod
    def build_routing_plane(
        *,
        route: ConditionalRouteOutcome,
        completed_turns: int,
        total_tokens_consumed: int,
        next_prompt_text: str,
        next_prompt_hash_hex_text: str,
    ) -> AdaptiveRoutingPlane:
        """Construct next immutable Plane 3 state from a resolved route outcome."""
        from talent_screener.domain.contexts.live_interview.value_objects import PromptHash

        return AdaptiveRoutingPlane(
            phase=route.next_phase,
            active_competency_id=route.next_competency_id,
            current_difficulty=route.next_difficulty,
            questions_asked_in_active_competency=route.next_questions_asked,
            probes_used_in_active_competency=route.next_probes_used,
            mastery_by_competency=route.updated_mastery_by_competency,
            rolling_authenticity_index=route.updated_authenticity_index,
            total_tokens_consumed=total_tokens_consumed,
            completed_turns=completed_turns,
            active_prompt_text=next_prompt_text,
            active_prompt_hash=PromptHash.from_prompt_text(next_prompt_hash_hex_text),
            last_routing_action=route.action,
            last_routing_rule_id=route.rule_id,
        )
