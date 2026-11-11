"""Use Case interactors for the Live Adaptive Interview Bounded Context."""

from __future__ import annotations

from uuid import UUID

from talent_screener.application.common.dtos import TokenUsageRecordDTO
from talent_screener.application.common.exceptions import (
    GuardrailViolationError,
    QuotaExceededError,
    ResourceConflictError,
    ResourceNotFoundError,
    UnauthorizedTenantAccessError,
)
from talent_screener.application.common.ports.outbound import (
    DistributedLockPort,
    ObservabilityTracerPort,
    TransactionalUnitOfWorkPort,
)
from talent_screener.application.live_interview.dtos import (
    CognitiveGraphContextDTO,
    CompetencyBriefingTargetDTO,
    CompleteInterviewSessionRequestDTO,
    CompleteInterviewSessionResponseDTO,
    InitializeInterviewSessionRequestDTO,
    InitializeInterviewSessionResponseDTO,
    InterviewInitCommandDTO,
    ProcessCandidateTurnRequestDTO,
    ProcessCandidateTurnResponseDTO,
    RecordClientTelemetryRequestDTO,
    RecordClientTelemetryResponseDTO,
    TerminateInterviewEarlyRequestDTO,
    TerminateInterviewEarlyResponseDTO,
)
from talent_screener.application.live_interview.ports.outbound import (
    AdaptiveInterviewGraphPort,
    AudioSttPort,
    AudioTtsPort,
    PromptGuardrailPort,
    TokenBudgetEnforcerPort,
)
from talent_screener.domain.contexts.document_intelligence import (
    CandidateProfileRepositoryPort,
    CandidateTier,
    SemanticMatchResultRepositoryPort,
)
from talent_screener.domain.contexts.identity_governance import (
    ConsentRecordRepositoryPort,
    TenantAccountRepositoryPort,
)
from talent_screener.domain.contexts.live_interview import (
    AdaptiveRoutingDomainService,
    AdaptiveTurn,
    IntegrityFlag,
    InterviewSession,
    InterviewSessionRepositoryPort,
    PromptHash,
    ResponseLatencyMetric,
    RoutingAction,
    SessionStatus,
    SocraticProbe,
    TelemetrySnapshot,
)
from talent_screener.domain.contexts.recruitment import (
    JobRequisitionRepositoryPort,
    RequisitionStatus,
)
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.value_objects import ConfidenceScore


class InitializeInterviewSessionUseCase:
    """Provisions and starts an adaptive InterviewSession anchored to rubric & skill gaps."""

    def __init__(
        self,
        *,
        tenant_repo: TenantAccountRepositoryPort,
        consent_repo: ConsentRecordRepositoryPort,
        requisition_repo: JobRequisitionRepositoryPort,
        profile_repo: CandidateProfileRepositoryPort,
        match_repo: SemanticMatchResultRepositoryPort,
        session_repo: InterviewSessionRepositoryPort,
        interview_graph: AdaptiveInterviewGraphPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._tenant_repo = tenant_repo
        self._consent_repo = consent_repo
        self._requisition_repo = requisition_repo
        self._profile_repo = profile_repo
        self._match_repo = match_repo
        self._session_repo = session_repo
        self._interview_graph = interview_graph
        self._uow = uow

    async def execute(
        self, request: InitializeInterviewSessionRequestDTO
    ) -> InitializeInterviewSessionResponseDTO:
        requisition = self._requisition_repo.get_by_id(request.tenant_id, request.requisition_id)
        if requisition is None:
            raise ResourceNotFoundError(
                f"JobRequisition {request.requisition_id} not found.",
                code="REQUISITION_NOT_FOUND",
            )
        if requisition.status is not RequisitionStatus.PUBLISHED:
            raise ResourceConflictError(
                "Cannot schedule interview for non-PUBLISHED JobRequisition.",
                code="REQUISITION_NOT_PUBLISHED",
            )

        profile = self._profile_repo.get_by_id(request.tenant_id, request.candidate_profile_id)
        if profile is None:
            raise ResourceNotFoundError(
                f"CandidateProfile {request.candidate_profile_id} not found.",
                code="PROFILE_NOT_FOUND",
            )

        consent = self._consent_repo.get_by_candidate_id(
            request.tenant_id, request.candidate_profile_id
        )
        if consent is not None and not consent.is_valid_for_processing():
            raise UnauthorizedTenantAccessError(
                "Candidate processing consent is missing, withdrawn, or expired.",
                code="CONSENT_INVALID",
            )

        match_result = self._match_repo.get_by_candidate_and_requisition(
            request.tenant_id, request.requisition_id, request.candidate_profile_id
        )
        if match_result is not None and match_result.tier is CandidateTier.GATEKEEPER_REJECTED:
            raise ResourceConflictError(
                "Candidate failed strict Hard-Skill Gatekeepers and cannot interview.",
                code="CANDIDATE_GATEKEEPER_REJECTED",
            )

        tenant = self._tenant_repo.get_by_id(request.tenant_id)
        if tenant is not None:
            try:
                tenant.consume_interview_quota()
            except InvariantViolationError as exc:
                if exc.code == "TENANT_SUSPENDED":
                    raise UnauthorizedTenantAccessError(exc.message, code=exc.code) from exc
                raise QuotaExceededError(exc.message, code=exc.code) from exc

        gap_priorities = (
            {gap.competency_id: gap.probe_priority for gap in match_result.skill_gaps}
            if match_result is not None
            else {}
        )
        # Prioritize Must-Have gatekeepers first (INV-LI-03), then descending EpistemicSkillGap
        ordered_criteria = sorted(
            requisition.criteria,
            key=lambda c: (1 if c.is_gatekeeper else 0, gap_priorities.get(c.id, c.weight)),
            reverse=True,
        )
        targets = tuple(
            CompetencyBriefingTargetDTO(
                competency_id=c.id,
                canonical_skill_id=c.canonical_skill_id,
                weight=c.weight,
                target_depth=c.target_depth,
                is_gatekeeper=c.is_gatekeeper,
                ideal_anchor_descriptor=c.ideal_anchor_descriptor or c.name,
                probe_priority=gap_priorities.get(c.id, c.weight),
            )
            for c in ordered_criteria
        )

        session = InterviewSession(
            tenant_id=request.tenant_id,
            requisition_id=requisition.id,
            candidate_profile_id=profile.id,
            rubric_version=requisition.rubric_version,
            competency_queue=[c.id for c in ordered_criteria],
            initial_difficulty=request.initial_difficulty,
            max_turns=request.max_turns,
            token_budget_limit=request.token_budget_limit,
        )
        session.initialize()

        project_highlights = tuple(p.summary for p in profile.projects[:5])
        init_outcome = await self._interview_graph.initialize_session_graph(
            InterviewInitCommandDTO(
                session_id=session.id,
                tenant_id=request.tenant_id,
                requisition_id=requisition.id,
                candidate_profile_id=profile.id,
                rubric_version=requisition.rubric_version,
                competency_targets=targets,
                candidate_project_highlights=project_highlights,
                max_wall_clock_seconds=requisition.interview_timebox_minutes * 60,
            )
        )

        session.start()

        async with self._uow:
            await self._uow.begin(request.tenant_id)
            if tenant is not None:
                self._tenant_repo.save(tenant)
                self._uow.register_aggregate(tenant)
            self._session_repo.save(session)
            self._uow.register_aggregate(session)
            await self._uow.commit()

        assert session.active_competency_id is not None
        return InitializeInterviewSessionResponseDTO(
            session_id=session.id,
            status=session.status,
            active_competency_id=session.active_competency_id,
            initial_difficulty=init_outcome.initial_difficulty,
            opening_question=init_outcome.opening_question,
            checkpoint_version=init_outcome.checkpoint_version,
        )


class ProcessCandidateTurnUseCase:
    """Executes guarded cognitive evaluation, Socratic routing, and turn persistence."""

    def __init__(
        self,
        *,
        session_repo: InterviewSessionRepositoryPort,
        requisition_repo: JobRequisitionRepositoryPort,
        profile_repo: CandidateProfileRepositoryPort,
        lock_port: DistributedLockPort,
        guardrail_port: PromptGuardrailPort,
        token_budget_enforcer: TokenBudgetEnforcerPort,
        interview_graph: AdaptiveInterviewGraphPort,
        stt_port: AudioSttPort,
        tts_port: AudioTtsPort,
        tracer: ObservabilityTracerPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._session_repo = session_repo
        self._requisition_repo = requisition_repo
        self._profile_repo = profile_repo
        self._lock_port = lock_port
        self._guardrail_port = guardrail_port
        self._token_budget_enforcer = token_budget_enforcer
        self._interview_graph = interview_graph
        self._stt_port = stt_port
        self._tts_port = tts_port
        self._tracer = tracer
        self._uow = uow
        self._routing_service = AdaptiveRoutingDomainService()

    def _resolve_next_competency(
        self, session: InterviewSession, current_comp_id: UUID
    ) -> UUID | None:
        queue = session.competency_queue
        if current_comp_id not in queue:
            return None
        idx = queue.index(current_comp_id)
        if idx + 1 < len(queue):
            return queue[idx + 1]
        return None

    async def execute(
        self, request: ProcessCandidateTurnRequestDTO
    ) -> ProcessCandidateTurnResponseDTO:
        with self._tracer.start_span(
            name="live_interview.process_turn",
            tenant_id=request.tenant_id,
            correlation_id=request.correlation_id,
            attributes={
                "session_id": str(request.session_id),
                "turn_number": request.turn_number,
            },
        ):
            lease = await self._lock_port.acquire(
                tenant_id=request.tenant_id,
                resource_key=f"interview_session:{request.session_id}",
            )
            try:
                session = self._session_repo.get_by_id(request.tenant_id, request.session_id)
                if session is None:
                    raise ResourceNotFoundError(
                        f"InterviewSession {request.session_id} not found.",
                        code="SESSION_NOT_FOUND",
                    )
                if session.status is not SessionStatus.IN_PROGRESS:
                    raise ResourceConflictError(
                        f"InterviewSession must be IN_PROGRESS; got {session.status.value}.",
                        code="SESSION_NOT_IN_PROGRESS",
                    )

                utterance = request.candidate_utterance.strip()
                cadence_wpm = request.telemetry.speech_cadence_wpm
                pause_entropy = request.telemetry.pause_entropy
                if not utterance and request.audio_bytes:
                    transcript = await self._stt_port.transcribe_audio(
                        tenant_id=request.tenant_id,
                        session_id=session.id,
                        audio_bytes=request.audio_bytes,
                    )
                    utterance = transcript.transcript_text
                    cadence_wpm = transcript.speech_cadence_wpm
                    pause_entropy = transcript.pause_entropy

                guardrail = await self._guardrail_port.evaluate_candidate_input(
                    tenant_id=request.tenant_id,
                    session_id=session.id,
                    turn_number=request.turn_number,
                    raw_utterance=utterance,
                )
                if guardrail.is_blocked:
                    block_flag = IntegrityFlag(
                        flag_code=guardrail.reason_code or "ADVERSARIAL_INJECTION_BLOCKED",
                        severity="CRITICAL",
                        turn_number=request.turn_number,
                        confidence=ConfidenceScore(min(1.0, max(0.0, guardrail.risk_score))),
                        evidence_summary="Prompt injection guardrail blocked untrusted input.",
                    )
                    snap = TelemetrySnapshot(
                        turn_number=request.turn_number,
                        focus_loss_count=request.telemetry.focus_loss_count,
                        focus_loss_duration_ms=request.telemetry.focus_loss_duration_ms,
                        external_paste_detected=request.telemetry.external_paste_detected,
                        paste_char_count=request.telemetry.paste_char_count,
                        keystroke_anomaly_detected=request.telemetry.keystroke_anomaly_detected,
                        response_latency=ResponseLatencyMetric(
                            ttft_ms=request.telemetry.response_latency_ms,
                            total_duration_ms=max(request.telemetry.response_latency_ms, 1),
                            speech_cadence_wpm=cadence_wpm,
                            pause_entropy=pause_entropy,
                        ),
                    )
                    async with self._uow:
                        await self._uow.begin(request.tenant_id)
                        session.record_telemetry_snapshot(snap, flags=(block_flag,))
                        self._session_repo.save(session)
                        self._uow.register_aggregate(session)
                        await self._uow.commit()
                    raise GuardrailViolationError(
                        "Candidate turn blocked by adversarial prompt injection guardrail.",
                        code=guardrail.reason_code or "PROMPT_INJECTION_BLOCKED",
                        metadata={"risk_score": guardrail.risk_score},
                    )

                await self._token_budget_enforcer.ensure_within_budget(
                    tenant_id=request.tenant_id,
                    session_id=session.id,
                    requested_tokens=500,
                    session_limit=session.token_budget_limit,
                )

                requisition = self._requisition_repo.get_by_id(
                    request.tenant_id, session.requisition_id
                )
                if requisition is None:
                    raise ResourceNotFoundError(
                        f"JobRequisition {session.requisition_id} not found.",
                        code="REQUISITION_NOT_FOUND",
                    )
                profile = self._profile_repo.get_by_id(
                    request.tenant_id, session.candidate_profile_id
                )
                highlights = tuple(p.summary for p in profile.projects[:5]) if profile else ()

                active_comp_id = session.active_competency_id or session.competency_queue[0]
                matching_criteria = [c for c in requisition.criteria if c.id == active_comp_id]
                criterion = matching_criteria[0] if matching_criteria else requisition.criteria[0]
                active_probe_count = session.probe_count_for(active_comp_id)
                primary_asked = sum(
                    1
                    for t in session.turns
                    if t.competency_id == active_comp_id and t.socratic_probe is None
                )

                graph_outcome = await self._interview_graph.execute_cognitive_turn(
                    CognitiveGraphContextDTO(
                        session_id=session.id,
                        tenant_id=request.tenant_id,
                        checkpoint_version=request.checkpoint_version,
                        turn_sequence_number=request.turn_number,
                        candidate_project_highlights=highlights,
                        active_competency=CompetencyBriefingTargetDTO(
                            competency_id=criterion.id,
                            canonical_skill_id=criterion.canonical_skill_id,
                            weight=criterion.weight,
                            target_depth=criterion.target_depth,
                            is_gatekeeper=criterion.is_gatekeeper,
                            ideal_anchor_descriptor=criterion.ideal_anchor_descriptor
                            or criterion.name,
                            probe_priority=criterion.weight,
                        ),
                        current_difficulty=session.current_difficulty,
                        socratic_followup_count=active_probe_count,
                        primary_questions_asked=primary_asked,
                        max_probes_allowed=2,
                        remaining_seconds=request.remaining_seconds,
                        sanitized_candidate_utterance=guardrail.sanitized_text,
                        turn_telemetry_signals=request.telemetry,
                    )
                )

                await self._token_budget_enforcer.record_consumption(
                    tenant_id=request.tenant_id,
                    session_id=session.id,
                    tokens_used=graph_outcome.tokens_used,
                )
                self._tracer.record_token_usage(
                    TokenUsageRecordDTO(
                        tenant_id=request.tenant_id,
                        session_id=session.id,
                        model_id="cognitive_interview_graph",
                        input_tokens=graph_outcome.tokens_used // 2,
                        output_tokens=graph_outcome.tokens_used - (graph_outcome.tokens_used // 2),
                        cost_micro_usd=graph_outcome.tokens_used * 2,
                    )
                )

                # Run deterministic domain routing verification
                comp_observations = [
                    (
                        t.difficulty,
                        t.evaluation.effective_quality_score(),
                        t.socratic_probe.probe_index if t.socratic_probe is not None else 0,
                    )
                    for t in session.turns
                    if t.competency_id == active_comp_id
                ]
                comp_observations.append(
                    (
                        session.current_difficulty,
                        graph_outcome.turn_evaluation.effective_quality_score(),
                        0,
                    )
                )
                current_mastery = self._routing_service.compute_competency_mastery(
                    comp_observations
                )
                idx_in_queue = (
                    session.competency_queue.index(active_comp_id)
                    if active_comp_id in session.competency_queue
                    else 0
                )
                remaining_comps = max(0, len(session.competency_queue) - idx_in_queue - 1)
                domain_route = self._routing_service.decide_next_route(
                    evaluation=graph_outcome.turn_evaluation,
                    current_difficulty=session.current_difficulty,
                    current_mastery=current_mastery,
                    target_mastery=float(criterion.target_depth),
                    active_probe_count=active_probe_count,
                    primary_questions_asked=primary_asked + 1,
                    completed_turns=request.turn_number,
                    max_turns=session.max_turns,
                    elapsed_seconds=float(
                        max(
                            0,
                            requisition.interview_timebox_minutes * 60 - request.remaining_seconds,
                        )
                    ),
                    max_wall_clock_seconds=float(requisition.interview_timebox_minutes * 60),
                    tokens_consumed=session.tokens_consumed + graph_outcome.tokens_used,
                    token_budget_limit=session.token_budget_limit,
                    remaining_competencies_count=remaining_comps,
                    turn_suspicion_risk=graph_outcome.synthetic_likelihood_score,
                )

                effective_action = graph_outcome.routing_action_taken
                if (
                    effective_action
                    in (
                        RoutingAction.SOCRATIC_DEPTH_PROBE,
                        RoutingAction.SOCRATIC_AUTHENTICITY_PROBE,
                    )
                    and active_probe_count >= 2
                ):
                    effective_action = domain_route.action

                probe_entity: SocraticProbe | None = None
                if effective_action in (
                    RoutingAction.SOCRATIC_DEPTH_PROBE,
                    RoutingAction.SOCRATIC_AUTHENTICITY_PROBE,
                ):
                    strategy = (
                        "AUTHENTICITY_VERIFICATION_PROBE"
                        if effective_action is RoutingAction.SOCRATIC_AUTHENTICITY_PROBE
                        else "UNSTATED_TRADEOFF_PROBE"
                    )
                    probe_entity = SocraticProbe(
                        competency_id=active_comp_id,
                        probe_index=active_probe_count + 1,
                        strategy=strategy,
                        target_concept=criterion.canonical_skill_id,
                        probe_prompt=graph_outcome.next_interviewer_utterance,
                        prompt_hash=graph_outcome.next_prompt_hash,
                    )

                flags_list: list[IntegrityFlag] = []
                if guardrail.is_suspicious:
                    flags_list.append(
                        IntegrityFlag(
                            flag_code="PROMPT_MANIPULATION_SUSPECTED",
                            severity="MEDIUM",
                            turn_number=request.turn_number,
                            confidence=ConfidenceScore(min(1.0, guardrail.risk_score)),
                            evidence_summary="Suspicious meta-framing stripped by guardrail.",
                        )
                    )

                turn_prompt = f"Assessment turn {request.turn_number} ({criterion.name})"
                turn = AdaptiveTurn(
                    turn_number=request.turn_number,
                    competency_id=active_comp_id,
                    difficulty=session.current_difficulty,
                    question_prompt=turn_prompt,
                    prompt_hash=PromptHash.from_prompt_text(turn_prompt),
                    candidate_response=guardrail.sanitized_text,
                    evaluation=graph_outcome.turn_evaluation,
                    tokens_used=graph_outcome.tokens_used,
                    socratic_probe=probe_entity,
                    latency_metric=ResponseLatencyMetric(
                        ttft_ms=request.telemetry.response_latency_ms,
                        total_duration_ms=max(request.telemetry.response_latency_ms, 1),
                        speech_cadence_wpm=cadence_wpm,
                        pause_entropy=pause_entropy,
                    ),
                    integrity_flags=tuple(flags_list),
                )
                telemetry_snapshot = TelemetrySnapshot(
                    turn_number=request.turn_number,
                    focus_loss_count=request.telemetry.focus_loss_count,
                    focus_loss_duration_ms=request.telemetry.focus_loss_duration_ms,
                    external_paste_detected=request.telemetry.external_paste_detected,
                    paste_char_count=request.telemetry.paste_char_count,
                    keystroke_anomaly_detected=request.telemetry.keystroke_anomaly_detected,
                    response_latency=ResponseLatencyMetric(
                        ttft_ms=request.telemetry.response_latency_ms,
                        total_duration_ms=max(request.telemetry.response_latency_ms, 1),
                        speech_cadence_wpm=cadence_wpm,
                        pause_entropy=pause_entropy,
                    ),
                )

                next_comp_id: UUID | None = None
                if effective_action is RoutingAction.TRANSITION_NEXT_COMPETENCY:
                    next_comp_id = self._resolve_next_competency(session, active_comp_id)

                session.record_turn(
                    turn,
                    telemetry=telemetry_snapshot,
                    next_difficulty=graph_outcome.next_difficulty,
                    next_competency_id=next_comp_id,
                )

                is_completed = effective_action is RoutingAction.CONCLUDE_INTERVIEW
                if is_completed:
                    session.begin_final_evaluation()
                    session.complete()

                async with self._uow:
                    await self._uow.begin(request.tenant_id)
                    self._session_repo.save(session)
                    self._uow.register_aggregate(session)
                    await self._uow.commit()

                audio_out = b""
                if request.synthesize_voice and graph_outcome.next_interviewer_utterance:
                    audio_out = await self._tts_port.synthesize_speech(
                        tenant_id=request.tenant_id,
                        session_id=session.id,
                        text=graph_outcome.next_interviewer_utterance,
                    )

                return ProcessCandidateTurnResponseDTO(
                    session_id=session.id,
                    turn_number=turn.turn_number,
                    session_status=session.status,
                    routing_action=effective_action,
                    next_difficulty=graph_outcome.next_difficulty,
                    next_question=graph_outcome.next_interviewer_utterance,
                    updated_checkpoint_version=graph_outcome.updated_checkpoint_version,
                    effective_quality_score=turn.evaluation.effective_quality_score(),
                    tokens_consumed=session.tokens_consumed,
                    is_completed=is_completed,
                    synthesized_audio=audio_out,
                )
            finally:
                await self._lock_port.release(lease=lease)


class RecordClientTelemetryUseCase:
    """Ingests client behavioral telemetry snapshots and raises IntegrityFlag alerts."""

    def __init__(
        self,
        *,
        session_repo: InterviewSessionRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._session_repo = session_repo
        self._uow = uow

    async def execute(
        self, request: RecordClientTelemetryRequestDTO
    ) -> RecordClientTelemetryResponseDTO:
        session = self._session_repo.get_by_id(request.tenant_id, request.session_id)
        if session is None:
            raise ResourceNotFoundError(
                f"InterviewSession {request.session_id} not found.",
                code="SESSION_NOT_FOUND",
            )
        snapshot = TelemetrySnapshot(
            turn_number=request.turn_number,
            focus_loss_count=request.focus_loss_count,
            focus_loss_duration_ms=request.focus_loss_duration_ms,
            external_paste_detected=request.external_paste_detected,
            paste_char_count=request.paste_char_count,
            keystroke_anomaly_detected=request.keystroke_anomaly_detected,
            response_latency=ResponseLatencyMetric(
                ttft_ms=request.ttft_ms,
                total_duration_ms=max(request.ttft_ms, request.total_duration_ms),
                speech_cadence_wpm=request.speech_cadence_wpm,
                pause_entropy=request.pause_entropy,
            ),
        )
        flags: list[IntegrityFlag] = []
        if request.external_paste_detected and request.paste_char_count >= 40:
            flags.append(
                IntegrityFlag(
                    flag_code="CLIPBOARD_PASTE_BURST",
                    severity="HIGH",
                    turn_number=request.turn_number,
                    confidence=ConfidenceScore(0.88),
                    evidence_summary=f"External paste of {request.paste_char_count} chars.",
                )
            )
        if request.focus_loss_count >= 2 or request.focus_loss_duration_ms >= 5000:
            flags.append(
                IntegrityFlag(
                    flag_code="SUSTAINED_FOCUS_LOSS",
                    severity="MEDIUM",
                    turn_number=request.turn_number,
                    confidence=ConfidenceScore(0.75),
                    evidence_summary=(
                        f"Window focus lost {request.focus_loss_count} time(s) "
                        f"({request.focus_loss_duration_ms}ms)."
                    ),
                )
            )
        if request.keystroke_anomaly_detected:
            flags.append(
                IntegrityFlag(
                    flag_code="KEYSTROKE_KINEMATIC_ANOMALY",
                    severity="HIGH",
                    turn_number=request.turn_number,
                    confidence=ConfidenceScore(0.85),
                    evidence_summary="Programmatic typing cadence anomaly detected.",
                )
            )

        async with self._uow:
            await self._uow.begin(request.tenant_id)
            session.record_telemetry_snapshot(snapshot, flags=flags)
            self._session_repo.save(session)
            self._uow.register_aggregate(session)
            await self._uow.commit()

        return RecordClientTelemetryResponseDTO(
            session_id=session.id,
            turn_number=request.turn_number,
            flags_recorded=len(flags),
            integrity_flag_codes=tuple(f.flag_code for f in flags),
        )


class TerminateInterviewEarlyUseCase:
    """Terminates an active or evaluating InterviewSession early with audit reason."""

    def __init__(
        self,
        *,
        session_repo: InterviewSessionRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._session_repo = session_repo
        self._uow = uow

    async def execute(
        self, request: TerminateInterviewEarlyRequestDTO
    ) -> TerminateInterviewEarlyResponseDTO:
        async with self._uow:
            await self._uow.begin(request.tenant_id)
            session = self._session_repo.get_by_id(request.tenant_id, request.session_id)
            if session is None:
                raise ResourceNotFoundError(
                    f"InterviewSession {request.session_id} not found.",
                    code="SESSION_NOT_FOUND",
                )
            session.terminate_early(request.reason)
            self._session_repo.save(session)
            self._uow.register_aggregate(session)
            await self._uow.commit()

        return TerminateInterviewEarlyResponseDTO(
            session_id=session.id,
            status=session.status,
            reason=session.termination_reason or request.reason,
            total_turns=len(session.turns),
        )


class CompleteInterviewSessionUseCase:
    """Finalizes cognitive graph execution and completes the InterviewSession."""

    def __init__(
        self,
        *,
        session_repo: InterviewSessionRepositoryPort,
        interview_graph: AdaptiveInterviewGraphPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._session_repo = session_repo
        self._interview_graph = interview_graph
        self._uow = uow

    async def execute(
        self, request: CompleteInterviewSessionRequestDTO
    ) -> CompleteInterviewSessionResponseDTO:
        session = self._session_repo.get_by_id(request.tenant_id, request.session_id)
        if session is None:
            raise ResourceNotFoundError(
                f"InterviewSession {request.session_id} not found.",
                code="SESSION_NOT_FOUND",
            )
        summary = await self._interview_graph.finalize_session_graph(
            tenant_id=request.tenant_id,
            session_id=session.id,
            checkpoint_version=request.checkpoint_version,
        )
        async with self._uow:
            await self._uow.begin(request.tenant_id)
            if session.status is SessionStatus.IN_PROGRESS:
                session.begin_final_evaluation()
            session.complete()
            self._session_repo.save(session)
            self._uow.register_aggregate(session)
            await self._uow.commit()

        return CompleteInterviewSessionResponseDTO(
            session_id=session.id,
            status=session.status,
            total_turns=len(session.turns),
            total_tokens_consumed=session.tokens_consumed,
            synthesis_summary=summary,
        )
