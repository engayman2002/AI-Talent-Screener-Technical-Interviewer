"""Live Interview Bounded Context Aggregate Root: InterviewSession."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from uuid import UUID

from talent_screener.domain.contexts.live_interview.entities import (
    AdaptiveTurn,
    SocraticProbe,
    TelemetrySnapshot,
)
from talent_screener.domain.contexts.live_interview.events import (
    AdaptiveTurnCompletedEvent,
    IntegrityAnomalyDetectedEvent,
    InterviewSessionCompletedEvent,
    InterviewSessionStartedEvent,
    InterviewSessionTerminatedEarlyEvent,
    SocraticProbeTriggeredEvent,
)
from talent_screener.domain.contexts.live_interview.exceptions import (
    InvalidTurnOrderError,
    SocraticProbeLimitExceededError,
    TokenBudgetExceededError,
    TurnLimitExceededError,
)
from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    IntegrityFlag,
    SessionStatus,
)
from talent_screener.domain.shared.base import AggregateRoot
from talent_screener.domain.shared.exceptions import (
    InvalidStateTransitionError,
    InvariantViolationError,
)
from talent_screener.domain.shared.guards import (
    require_int_range,
    require_non_empty_str,
    require_non_nil_uuid,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import TenantId, VersionNumber

MAX_SOCRATIC_PROBES_PER_COMPETENCY: int = 2
DEFAULT_TOKEN_BUDGET_LIMIT: int = 45_000
DEFAULT_MAX_TURNS: int = 16

_ALLOWED_SESSION_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.SCHEDULED: frozenset({SessionStatus.INITIALIZING, SessionStatus.ABANDONED}),
    SessionStatus.INITIALIZING: frozenset({SessionStatus.IN_PROGRESS, SessionStatus.ABANDONED}),
    SessionStatus.IN_PROGRESS: frozenset(
        {
            SessionStatus.EVALUATING_FINAL,
            SessionStatus.TERMINATED_EARLY,
            SessionStatus.ABANDONED,
        }
    ),
    SessionStatus.EVALUATING_FINAL: frozenset(
        {
            SessionStatus.COMPLETED,
            SessionStatus.TERMINATED_EARLY,
            SessionStatus.ABANDONED,
        }
    ),
    SessionStatus.COMPLETED: frozenset(),
    SessionStatus.TERMINATED_EARLY: frozenset(),
    SessionStatus.ABANDONED: frozenset(),
}

_SEVERITY_RANK: dict[str, int] = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


class InterviewSession(AggregateRoot[UUID]):
    """Stateful interview aggregate enforcing turn ordering, probe caps, and token budgets."""

    __slots__ = (
        "_active_competency_id",
        "_candidate_profile_id",
        "_competency_queue",
        "_current_difficulty",
        "_integrity_flags",
        "_max_turns",
        "_probe_counts_by_competency",
        "_requisition_id",
        "_rubric_version",
        "_status",
        "_telemetry_snapshots",
        "_termination_reason",
        "_token_budget_limit",
        "_tokens_consumed",
        "_turns",
    )

    def __init__(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        rubric_version: VersionNumber,
        competency_queue: Sequence[UUID],
        initial_difficulty: DifficultyLevel = DifficultyLevel.L3,
        max_turns: int = DEFAULT_MAX_TURNS,
        token_budget_limit: int = DEFAULT_TOKEN_BUDGET_LIMIT,
        session_id: UUID | None = None,
        version: VersionNumber | None = None,
    ) -> None:
        resolved_id = session_id if session_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "session_id", code="INVALID_SESSION_ID")
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
        comp_tuple = tuple(competency_queue)
        if not comp_tuple:
            raise InvariantViolationError(
                "InterviewSession requires at least one target competency.",
                code="EMPTY_COMPETENCY_QUEUE",
            )
        for comp_id in comp_tuple:
            require_non_nil_uuid(comp_id, "competency_id", code="INVALID_COMPETENCY_ID")

        self._requisition_id: UUID = requisition_id
        self._candidate_profile_id: UUID = candidate_profile_id
        self._rubric_version: VersionNumber = rubric_version
        self._competency_queue: tuple[UUID, ...] = comp_tuple
        self._active_competency_id: UUID | None = None
        self._current_difficulty: DifficultyLevel = DifficultyLevel.from_int(
            int(initial_difficulty)
        )
        self._max_turns: int = require_int_range(
            max_turns, "max_turns", 1, 50, code="INVALID_MAX_TURNS"
        )
        self._token_budget_limit: int = require_int_range(
            token_budget_limit,
            "token_budget_limit",
            100,
            1_000_000,
            code="INVALID_TOKEN_BUDGET",
        )
        self._tokens_consumed: int = 0
        self._status: SessionStatus = SessionStatus.SCHEDULED
        self._turns: list[AdaptiveTurn] = []
        self._telemetry_snapshots: list[TelemetrySnapshot] = []
        self._integrity_flags: list[IntegrityFlag] = []
        self._probe_counts_by_competency: dict[UUID, int] = {}
        self._termination_reason: str | None = None

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
    def status(self) -> SessionStatus:
        return self._status

    @property
    def competency_queue(self) -> tuple[UUID, ...]:
        return self._competency_queue

    @property
    def active_competency_id(self) -> UUID | None:
        return self._active_competency_id

    @property
    def current_difficulty(self) -> DifficultyLevel:
        return self._current_difficulty

    @property
    def max_turns(self) -> int:
        return self._max_turns

    @property
    def token_budget_limit(self) -> int:
        return self._token_budget_limit

    @property
    def tokens_consumed(self) -> int:
        return self._tokens_consumed

    @property
    def turns(self) -> tuple[AdaptiveTurn, ...]:
        return tuple(self._turns)

    @property
    def telemetry_snapshots(self) -> tuple[TelemetrySnapshot, ...]:
        return tuple(self._telemetry_snapshots)

    @property
    def integrity_flags(self) -> tuple[IntegrityFlag, ...]:
        return tuple(self._integrity_flags)

    @property
    def probe_counts_by_competency(self) -> Mapping[UUID, int]:
        return MappingProxyType(dict(self._probe_counts_by_competency))

    @property
    def termination_reason(self) -> str | None:
        return self._termination_reason

    def probe_count_for(self, competency_id: UUID) -> int:
        """Return number of Socratic probes already executed for a competency."""
        return self._probe_counts_by_competency.get(competency_id, 0)

    def _transition_to(self, target: SessionStatus) -> None:
        allowed = _ALLOWED_SESSION_TRANSITIONS[self._status]
        if target not in allowed:
            raise InvalidStateTransitionError(
                f"Cannot transition InterviewSession from {self._status} to {target}.",
                from_state=self._status.value,
                to_state=target.value,
                code="INVALID_SESSION_TRANSITION",
            )
        self._status = target
        self._increment_version()

    def initialize(self) -> None:
        """Transition SCHEDULED -> INITIALIZING prior to live audio/text start."""
        self._transition_to(SessionStatus.INITIALIZING)

    def start(self) -> None:
        """Transition INITIALIZING -> IN_PROGRESS and activate first competency."""
        self._transition_to(SessionStatus.IN_PROGRESS)
        self._active_competency_id = self._competency_queue[0]
        self._record_event(
            InterviewSessionStartedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                session_id=self._id,
                requisition_id=self._requisition_id,
                candidate_profile_id=self._candidate_profile_id,
                initial_competency_id=self._active_competency_id,
            )
        )

    def begin_final_evaluation(self) -> None:
        """Transition IN_PROGRESS -> EVALUATING_FINAL."""
        self._transition_to(SessionStatus.EVALUATING_FINAL)

    def complete(self) -> None:
        """Transition EVALUATING_FINAL -> COMPLETED."""
        self._transition_to(SessionStatus.COMPLETED)
        self._active_competency_id = None
        self._record_event(
            InterviewSessionCompletedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                session_id=self._id,
                total_turns=len(self._turns),
                total_tokens_consumed=self._tokens_consumed,
            )
        )

    def terminate_early(self, reason: str) -> None:
        """Transition IN_PROGRESS or EVALUATING_FINAL -> TERMINATED_EARLY."""
        cleaned = require_non_empty_str(reason, "reason", max_len=250, code="INVALID_REASON")
        self._transition_to(SessionStatus.TERMINATED_EARLY)
        self._termination_reason = cleaned
        self._active_competency_id = None
        self._record_event(
            InterviewSessionTerminatedEarlyEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                session_id=self._id,
                terminal_status=self._status.value,
                reason=cleaned,
                total_turns=len(self._turns),
            )
        )

    def abandon(self, reason: str = "Session abandoned by disconnect or expiration.") -> None:
        """Transition active/scheduled session -> ABANDONED."""
        cleaned = require_non_empty_str(reason, "reason", max_len=250, code="INVALID_REASON")
        self._transition_to(SessionStatus.ABANDONED)
        self._termination_reason = cleaned
        self._active_competency_id = None
        self._record_event(
            InterviewSessionTerminatedEarlyEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                session_id=self._id,
                terminal_status=self._status.value,
                reason=cleaned,
                total_turns=len(self._turns),
            )
        )

    def record_turn(
        self,
        turn: AdaptiveTurn,
        *,
        telemetry: TelemetrySnapshot | None = None,
        next_difficulty: DifficultyLevel | None = None,
        next_competency_id: UUID | None = None,
    ) -> None:
        """Append a strictly ordered AdaptiveTurn enforcing probe caps and token budgets."""
        if self._status is not SessionStatus.IN_PROGRESS:
            raise InvalidStateTransitionError(
                f"Turns can only be recorded while IN_PROGRESS; current status is {self._status}.",
                from_state=self._status.value,
                code="SESSION_NOT_IN_PROGRESS",
            )
        expected_turn_number = len(self._turns) + 1
        if turn.turn_number != expected_turn_number:
            raise InvalidTurnOrderError(
                f"Expected monotonic turn_number={expected_turn_number}; got {turn.turn_number}.",
            )
        if expected_turn_number > self._max_turns:
            raise TurnLimitExceededError(
                f"Cannot exceed max_turns={self._max_turns} for InterviewSession.",
            )
        projected_tokens = self._tokens_consumed + turn.tokens_used
        if projected_tokens > self._token_budget_limit:
            raise TokenBudgetExceededError(
                f"Turn requires {turn.tokens_used} tokens, exceeding session budget "
                f"({projected_tokens} > {self._token_budget_limit}).",
            )

        if turn.socratic_probe is not None:
            self._register_socratic_probe(turn.socratic_probe, turn.turn_number)

        self._turns.append(turn)
        self._tokens_consumed = projected_tokens
        if telemetry is not None:
            if telemetry.turn_number != turn.turn_number:
                raise InvalidTurnOrderError(
                    f"TelemetrySnapshot turn_number ({telemetry.turn_number}) must match "
                    f"AdaptiveTurn turn_number ({turn.turn_number}).",
                )
            self._telemetry_snapshots.append(telemetry)

        if turn.integrity_flags:
            self._integrity_flags.extend(turn.integrity_flags)
            max_flag = max(
                turn.integrity_flags,
                key=lambda fl: _SEVERITY_RANK.get(fl.severity, 0),
            )
            self._record_event(
                IntegrityAnomalyDetectedEvent(
                    aggregate_id=self._id,
                    tenant_id=self._tenant_id,
                    session_id=self._id,
                    turn_number=turn.turn_number,
                    flag_codes=tuple(fl.flag_code for fl in turn.integrity_flags),
                    max_severity=max_flag.severity,
                )
            )

        if next_difficulty is not None:
            self._current_difficulty = DifficultyLevel.from_int(int(next_difficulty))
        if next_competency_id is not None:
            if next_competency_id not in self._competency_queue:
                raise InvariantViolationError(
                    f"next_competency_id {next_competency_id} is not in session competency_queue.",
                    code="INVALID_COMPETENCY_ID",
                )
            self._active_competency_id = next_competency_id

        self._increment_version()
        self._record_event(
            AdaptiveTurnCompletedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                session_id=self._id,
                turn_number=turn.turn_number,
                competency_id=turn.competency_id,
                difficulty_level=int(turn.difficulty),
                effective_quality_score=turn.evaluation.effective_quality_score(),
                tokens_used=turn.tokens_used,
            )
        )

    def record_telemetry_snapshot(
        self,
        snapshot: TelemetrySnapshot,
        *,
        flags: Sequence[IntegrityFlag] = (),
    ) -> None:
        """Append a standalone client integrity telemetry snapshot during an active session."""
        if self._status is not SessionStatus.IN_PROGRESS:
            raise InvalidStateTransitionError(
                f"Telemetry can only be recorded while IN_PROGRESS; got {self._status}.",
                from_state=self._status.value,
                code="SESSION_NOT_IN_PROGRESS",
            )
        self._telemetry_snapshots.append(snapshot)
        if flags:
            self._integrity_flags.extend(flags)
            max_flag = max(flags, key=lambda fl: _SEVERITY_RANK.get(fl.severity, 0))
            self._record_event(
                IntegrityAnomalyDetectedEvent(
                    aggregate_id=self._id,
                    tenant_id=self._tenant_id,
                    session_id=self._id,
                    turn_number=snapshot.turn_number,
                    flag_codes=tuple(fl.flag_code for fl in flags),
                    max_severity=max_flag.severity,
                )
            )
        self._increment_version()

    def _register_socratic_probe(self, probe: SocraticProbe, turn_number: int) -> None:
        current_count = self._probe_counts_by_competency.get(probe.competency_id, 0)
        if current_count >= MAX_SOCRATIC_PROBES_PER_COMPETENCY:
            raise SocraticProbeLimitExceededError(
                f"Competency {probe.competency_id} already reached the maximum of "
                f"{MAX_SOCRATIC_PROBES_PER_COMPETENCY} Socratic probes.",
            )
        next_count = current_count + 1
        if probe.probe_index != next_count:
            raise InvariantViolationError(
                f"Expected SocraticProbe probe_index={next_count}; got {probe.probe_index}.",
                code="INVALID_PROBE_INDEX",
            )
        self._probe_counts_by_competency[probe.competency_id] = next_count
        self._record_event(
            SocraticProbeTriggeredEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                session_id=self._id,
                turn_number=turn_number,
                competency_id=probe.competency_id,
                probe_index=probe.probe_index,
                strategy=probe.strategy,
            )
        )
