"""LangGraphAdaptiveInterviewAdapter implementing Application AdaptiveInterviewGraphPort."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from talent_screener.application.common.exceptions import (
    ExternalAdapterFailureError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from talent_screener.application.live_interview.dtos import (
    CognitiveGraphContextDTO,
    CognitiveGraphResultDTO,
    InterviewInitCommandDTO,
    InterviewInitOutcomeDTO,
)
from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    IntegrityFlag,
    PromptHash,
    RoutingAction,
)
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import ConfidenceScore, TenantId
from talent_screener.infrastructure.ai_adapters.langgraph_interview.checkpointer import (
    InMemoryFencedStateCheckpointer,
    InterviewStateCheckpointerProtocol,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview.nodes import (
    EvaluationNode,
    InterviewSynthesisModelProtocol,
    QuestionGenerationModelProtocol,
    QuestionGeneratorNode,
    ScoringVerificationNode,
    SocraticFollowUpNode,
    SocraticProbeModelProtocol,
    TurnEvaluationModelProtocol,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview.router import (
    AdaptiveEdgeRouter,
    ConditionalRouteOutcome,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview.state import (
    AdaptiveRoutingPlane,
    ClientTelemetryRecord,
    EvaluatedTurnRecord,
    InterviewGraphPhase,
    InterviewGraphState,
    PromptHashAuditRecord,
    SocraticProbeState,
    StateReducer,
    TurnTranscriptRecord,
    build_initial_graph_state,
    derive_checkpoint_version,
)

_CLOSING_UTTERANCE: str = (
    "Thank you for walking through your engineering approach and architectural tradeoffs. "
    "That concludes our adaptive technical assessment."
)


class LangGraphAdaptiveInterviewAdapter:
    """Stateful 4-Plane LangGraph execution engine fulfilling AdaptiveInterviewGraphPort."""

    def __init__(
        self,
        *,
        checkpointer: InterviewStateCheckpointerProtocol | None = None,
        question_model: QuestionGenerationModelProtocol | None = None,
        evaluation_model: TurnEvaluationModelProtocol | None = None,
        socratic_model: SocraticProbeModelProtocol | None = None,
        synthesis_model: InterviewSynthesisModelProtocol | None = None,
        router: AdaptiveEdgeRouter | None = None,
        recent_window_size: int = 8,
    ) -> None:
        self._checkpointer: InterviewStateCheckpointerProtocol = (
            checkpointer if checkpointer is not None else InMemoryFencedStateCheckpointer()
        )
        self._question_node = QuestionGeneratorNode(model=question_model)
        self._evaluation_node = EvaluationNode(model=evaluation_model)
        self._socratic_node = SocraticFollowUpNode(model=socratic_model)
        self._verification_node = ScoringVerificationNode(model=synthesis_model)
        self._router = router if router is not None else AdaptiveEdgeRouter()
        self._recent_window_size = max(1, recent_window_size)

    @property
    def checkpointer(self) -> InterviewStateCheckpointerProtocol:
        """Expose underlying checkpointer for observability and checkpoint inspection."""
        return self._checkpointer

    async def initialize_session_graph(
        self, command: InterviewInitCommandDTO
    ) -> InterviewInitOutcomeDTO:
        """Seed Plane 1-4 state, generate opening prompt, and persist initial checkpoint."""
        if not command.competency_targets:
            raise ExternalAdapterFailureError(
                "Cannot initialize adaptive interview graph with zero competencies.",
                code="EMPTY_COMPETENCY_TARGETS",
                retryable=False,
            )

        lead_target = command.competency_targets[0]
        initial_difficulty = DifficultyLevel.L3
        opening_question, opening_hash, opening_tokens = await self._question_node.run(
            competency=lead_target,
            difficulty=initial_difficulty,
            project_highlights=command.candidate_project_highlights,
            turn_number=0,
        )
        state = build_initial_graph_state(
            session_id=command.session_id,
            tenant_id=command.tenant_id,
            requisition_id=command.requisition_id,
            candidate_profile_id=command.candidate_profile_id,
            rubric_version=command.rubric_version,
            competency_targets=command.competency_targets,
            candidate_project_highlights=command.candidate_project_highlights,
            opening_question=opening_question,
            opening_prompt_hash=opening_hash,
            opening_tokens_used=opening_tokens,
            max_wall_clock_seconds=command.max_wall_clock_seconds,
            max_probes_per_competency=command.max_probes_per_competency,
            initial_difficulty=initial_difficulty,
            fencing_token=1,
        )
        self._checkpointer.save_checkpoint(state)
        return InterviewInitOutcomeDTO(
            opening_question=opening_question,
            opening_prompt_hash=opening_hash,
            initial_competency_id=lead_target.competency_id,
            initial_difficulty=initial_difficulty,
            checkpoint_version=state.checkpoint_version,
        )

    async def execute_cognitive_turn(
        self, context: CognitiveGraphContextDTO
    ) -> CognitiveGraphResultDTO:
        """Execute Evaluation -> Router -> FollowUp/Question/Synthesis -> Checkpoint step."""
        snapshot = self._checkpointer.load_latest(context.tenant_id, context.session_id)
        if snapshot is None:
            raise ResourceNotFoundError(
                f"No graph checkpoint found for session {context.session_id}.",
                code="GRAPH_CHECKPOINT_NOT_FOUND",
            )
        if snapshot.checkpoint_version != context.checkpoint_version:
            raise ResourceConflictError(
                (
                    f"Stale checkpoint version {context.checkpoint_version!r}; "
                    f"active checkpoint is {snapshot.checkpoint_version!r}."
                ),
                code="CHECKPOINT_VERSION_CONFLICT",
                metadata={
                    "provided_checkpoint": context.checkpoint_version,
                    "active_checkpoint": snapshot.checkpoint_version,
                },
            )

        state = snapshot.state
        prior_hashes = tuple(entry.prompt_hash for entry in state.ledger.prompt_hashes)
        prior_questions = tuple(item.question_prompt for item in state.ledger.turns)
        active_comp = context.active_competency

        eval_payload = await self._evaluation_node.run(
            competency=active_comp,
            difficulty=context.current_difficulty,
            turn_number=context.turn_sequence_number,
            active_question=state.routing.active_prompt_text,
            sanitized_candidate_utterance=context.sanitized_candidate_utterance,
            project_highlights=context.candidate_project_highlights,
        )

        projected_tokens = state.routing.total_tokens_consumed + eval_payload.tokens_used
        route = self._router.evaluate_route(
            state=state,
            context=context,
            eval_payload=eval_payload,
            projected_total_tokens=projected_tokens,
        )

        probe_record: SocraticProbeState | None = None
        next_prompt_text: str
        next_prompt_hash: PromptHash
        generation_tokens: int
        prompt_role: str

        if route.action in (
            RoutingAction.SOCRATIC_DEPTH_PROBE,
            RoutingAction.SOCRATIC_AUTHENTICITY_PROBE,
        ):
            strategy = (
                "AUTHENTICITY_VERIFICATION_PROBE"
                if route.action is RoutingAction.SOCRATIC_AUTHENTICITY_PROBE
                else (
                    "SHALLOW_BUZZWORD_PROBE"
                    if eval_payload.specificity_norm < 0.48
                    else "UNSTATED_TRADEOFF_PROBE"
                )
            )
            chosen_strategy, next_prompt_text, next_prompt_hash, generation_tokens = (
                await self._socratic_node.run(
                    competency=active_comp,
                    difficulty=route.next_difficulty,
                    probe_index=route.next_probes_used,
                    strategy=strategy,
                    sanitized_candidate_utterance=context.sanitized_candidate_utterance,
                    rationale=eval_payload.rationale,
                    prior_prompt_hashes=prior_hashes,
                )
            )
            probe_record = SocraticProbeState(
                turn_number=context.turn_sequence_number,
                competency_id=active_comp.competency_id,
                probe_index=route.next_probes_used,
                strategy=chosen_strategy,
                target_concept=active_comp.canonical_skill_id,
                probe_prompt=next_prompt_text,
                prompt_hash=next_prompt_hash,
            )
            prompt_role = chosen_strategy
        elif route.action is RoutingAction.CONCLUDE_INTERVIEW:
            next_prompt_text = _CLOSING_UTTERANCE
            next_prompt_hash = PromptHash.from_prompt_text(next_prompt_text)
            prompt_role = "CONCLUSION"
            generation_tokens = 0
        else:
            next_target = state.rubric.competency_by_id(route.next_competency_id)
            next_prompt_text, next_prompt_hash, generation_tokens = await self._question_node.run(
                competency=next_target,
                difficulty=route.next_difficulty,
                project_highlights=context.candidate_project_highlights,
                turn_number=context.turn_sequence_number + 1,
                prior_questions=prior_questions,
                prior_prompt_hashes=prior_hashes,
            )
            prompt_role = (
                "TRANSITION_QUESTION"
                if route.action is RoutingAction.TRANSITION_NEXT_COMPETENCY
                else "ESCALATED_QUESTION"
            )

        turn_tokens = eval_payload.tokens_used + generation_tokens
        total_tokens = state.routing.total_tokens_consumed + turn_tokens

        turn_record = TurnTranscriptRecord(
            turn_number=context.turn_sequence_number,
            competency_id=active_comp.competency_id,
            question_prompt=state.routing.active_prompt_text,
            candidate_response=context.sanitized_candidate_utterance,
            difficulty=context.current_difficulty,
            is_socratic_probe=state.routing.phase is InterviewGraphPhase.SOCRATIC_PROBING,
            probe_index=probe_record.probe_index if probe_record is not None else 0,
            recorded_at=utc_now(),
        )
        eval_record = EvaluatedTurnRecord(
            turn_number=context.turn_sequence_number,
            competency_id=active_comp.competency_id,
            difficulty=context.current_difficulty,
            evaluation=eval_payload.turn_evaluation,
            weighted_turn_score=eval_payload.weighted_turn_score,
            synthetic_likelihood_score=route.turn_suspicion_risk,
            probe_index=probe_record.probe_index if probe_record is not None else 0,
            rationale=eval_payload.rationale,
        )
        telemetry_record = ClientTelemetryRecord(
            turn_number=context.turn_sequence_number,
            payload=context.turn_telemetry_signals,
            p_client=route.p_client,
            p_cadence=route.p_cadence,
            p_stylo=route.p_stylo,
            turn_suspicion_risk=route.turn_suspicion_risk,
        )
        flags = self._build_turn_integrity_flags(context=context, route=route)

        next_routing = AdaptiveRoutingPlane(
            phase=route.next_phase,
            active_competency_id=route.next_competency_id,
            current_difficulty=route.next_difficulty,
            questions_asked_in_active_competency=route.next_questions_asked,
            probes_used_in_active_competency=route.next_probes_used,
            mastery_by_competency=route.updated_mastery_by_competency,
            rolling_authenticity_index=route.updated_authenticity_index,
            total_tokens_consumed=total_tokens,
            completed_turns=context.turn_sequence_number,
            active_prompt_text=next_prompt_text,
            active_prompt_hash=next_prompt_hash,
            last_routing_action=route.action,
            last_routing_rule_id=route.rule_id,
        )
        new_fence = snapshot.fencing_token + 1
        updated_state = StateReducer.with_recorded_turn(
            state,
            turn=turn_record,
            evaluation=eval_record,
            telemetry=telemetry_record,
            next_routing=next_routing,
            new_fencing_token=new_fence,
            probe=probe_record,
            next_prompt_audit=PromptHashAuditRecord(
                turn_number=context.turn_sequence_number,
                role=prompt_role,
                prompt_hash=next_prompt_hash,
            ),
            flags=flags,
            window_size=self._recent_window_size,
        )

        if route.action is RoutingAction.CONCLUDE_INTERVIEW:
            verification = await self._verification_node.run(updated_state)
            turn_tokens += verification.tokens_used
            updated_state = self._with_verification_summary(
                updated_state,
                summary_text=verification.summary_text,
                additional_tokens=verification.tokens_used,
            )

        self._checkpointer.save_checkpoint(
            updated_state,
            expected_previous_version=context.checkpoint_version,
        )

        return CognitiveGraphResultDTO(
            turn_evaluation=eval_payload.turn_evaluation,
            synthetic_likelihood_score=route.turn_suspicion_risk,
            routing_action_taken=route.action,
            next_difficulty=route.next_difficulty,
            next_interviewer_utterance=next_prompt_text,
            next_prompt_hash=next_prompt_hash,
            updated_checkpoint_version=updated_state.checkpoint_version,
            tokens_used=turn_tokens,
        )

    async def finalize_session_graph(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        checkpoint_version: str,
    ) -> str:
        """Run final ScoringVerificationNode synthesis and seal COMPLETED checkpoint."""
        snapshot = self._checkpointer.load_latest(tenant_id, session_id)
        if snapshot is None:
            raise ResourceNotFoundError(
                f"No graph checkpoint found for session {session_id}.",
                code="GRAPH_CHECKPOINT_NOT_FOUND",
            )
        if checkpoint_version and snapshot.checkpoint_version != checkpoint_version:
            restored = self._checkpointer.load_version(tenant_id, session_id, checkpoint_version)
            if restored is None:
                raise ResourceConflictError(
                    (
                        f"Unknown or mismatched checkpoint version {checkpoint_version!r} "
                        f"for session {session_id}."
                    ),
                    code="CHECKPOINT_VERSION_CONFLICT",
                )

        if snapshot.state.routing.synthesis_summary is not None:
            return snapshot.state.routing.synthesis_summary

        verification = await self._verification_node.run(snapshot.state)
        final_routing = replace(
            snapshot.state.routing,
            phase=InterviewGraphPhase.COMPLETED,
            total_tokens_consumed=(
                snapshot.state.routing.total_tokens_consumed + verification.tokens_used
            ),
            synthesis_summary=verification.summary_text,
        )
        new_fence = snapshot.fencing_token + 1
        final_ckpt = derive_checkpoint_version(
            session_id=session_id,
            fencing_token=new_fence,
            completed_turns=snapshot.state.routing.completed_turns,
            prompt_hash=snapshot.state.routing.active_prompt_hash,
        )
        sealed_state = replace(
            snapshot.state,
            routing=final_routing,
            checkpoint_version=final_ckpt,
            fencing_token=new_fence,
        )
        self._checkpointer.save_checkpoint(sealed_state)
        return verification.summary_text

    @staticmethod
    def _with_verification_summary(
        state: InterviewGraphState,
        *,
        summary_text: str,
        additional_tokens: int,
    ) -> InterviewGraphState:
        sealed_routing = replace(
            state.routing,
            phase=InterviewGraphPhase.COMPLETED,
            total_tokens_consumed=state.routing.total_tokens_consumed + additional_tokens,
            synthesis_summary=summary_text,
        )
        return replace(state, routing=sealed_routing)

    @staticmethod
    def _build_turn_integrity_flags(
        *,
        context: CognitiveGraphContextDTO,
        route: ConditionalRouteOutcome,
    ) -> tuple[IntegrityFlag, ...]:
        flags: list[IntegrityFlag] = []
        if route.turn_suspicion_risk >= 0.60:
            flags.append(
                IntegrityFlag(
                    flag_code="CORROBORATED_SUSPICION_THRESHOLD",
                    severity="HIGH" if route.turn_suspicion_risk >= 0.75 else "MEDIUM",
                    turn_number=context.turn_sequence_number,
                    confidence=ConfidenceScore(min(1.0, route.turn_suspicion_risk)),
                    evidence_summary=(
                        f"Turn R_turn={route.turn_suspicion_risk:.2f} "
                        f"(P_client={route.p_client:.2f}, P_stylo={route.p_stylo:.2f})."
                    ),
                )
            )
        return tuple(flags)
