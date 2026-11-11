"""ADR-005 4-Plane Adaptive Interview Graph State Schema & Idempotent Reducers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from uuid import UUID

from talent_screener.application.live_interview.dtos import (
    ClientTelemetryPayloadDTO,
    CompetencyBriefingTargetDTO,
)
from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    IntegrityFlag,
    PromptHash,
    RoutingAction,
    TurnEvaluationScore,
)
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import TenantId, VersionNumber

DEFAULT_RECENT_WINDOW_SIZE: int = 8


class InterviewGraphPhase(StrEnum):
    """Macro lifecycle stage of the stateful LangGraph interview execution."""

    INITIALIZING = "INITIALIZING"
    QUESTION_GENERATED = "QUESTION_GENERATED"
    EVALUATING_TURN = "EVALUATING_TURN"
    SOCRATIC_PROBING = "SOCRATIC_PROBING"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True, slots=True)
class SessionMetadataPlane:
    """Plane 1: Immutable session identity, tenant boundary, and global execution budgets."""

    session_id: UUID
    tenant_id: TenantId
    requisition_id: UUID
    candidate_profile_id: UUID
    rubric_version: VersionNumber
    started_at: datetime
    max_wall_clock_seconds: int
    max_turns: int = 16
    token_budget_limit: int = 45_000
    max_probes_per_competency: int = 2


@dataclass(frozen=True, slots=True)
class RubricContextPlane:
    """Plane 2: Read-only competency target queue and grounded candidate project highlights."""

    competency_queue: tuple[CompetencyBriefingTargetDTO, ...]
    candidate_project_highlights: tuple[str, ...]

    def competency_by_id(self, competency_id: UUID) -> CompetencyBriefingTargetDTO:
        """Look up target briefing by competency UUID, falling back to queue head."""
        for target in self.competency_queue:
            if target.competency_id == competency_id:
                return target
        return self.competency_queue[0]

    def remaining_count_after(self, competency_id: UUID) -> int:
        """Return number of queued competencies strictly after ``competency_id``."""
        for idx, target in enumerate(self.competency_queue):
            if target.competency_id == competency_id:
                return max(0, len(self.competency_queue) - idx - 1)
        return 0

    def next_competency_after(self, competency_id: UUID) -> CompetencyBriefingTargetDTO | None:
        """Return the subsequent queued competency or ``None`` if at queue end."""
        for idx, target in enumerate(self.competency_queue):
            if target.competency_id == competency_id and idx + 1 < len(self.competency_queue):
                return self.competency_queue[idx + 1]
        return None


@dataclass(frozen=True, slots=True)
class TurnTranscriptRecord:
    """Immutable dialogue record stored in Plane 4 bounded append-only ledger."""

    turn_number: int
    competency_id: UUID
    question_prompt: str
    candidate_response: str
    difficulty: DifficultyLevel
    is_socratic_probe: bool
    probe_index: int
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class EvaluatedTurnRecord:
    """Immutable cognitive rubric score and stylometric suspicion record per turn."""

    turn_number: int
    competency_id: UUID
    difficulty: DifficultyLevel
    evaluation: TurnEvaluationScore
    weighted_turn_score: float
    synthetic_likelihood_score: float
    probe_index: int
    rationale: str


@dataclass(frozen=True, slots=True)
class SocraticProbeState:
    """Audit record for generated Socratic depth or authenticity follow-up probes."""

    turn_number: int
    competency_id: UUID
    probe_index: int
    strategy: str
    target_concept: str
    probe_prompt: str
    prompt_hash: PromptHash


@dataclass(frozen=True, slots=True)
class PromptHashAuditRecord:
    """Deterministic prompt hash entry preventing duplicate interview prompts."""

    turn_number: int
    role: str
    prompt_hash: PromptHash


@dataclass(frozen=True, slots=True)
class ClientTelemetryRecord:
    """Turn-scoped behavioral telemetry and computed authenticity suspicion components."""

    turn_number: int
    payload: ClientTelemetryPayloadDTO
    p_client: float
    p_cadence: float
    p_stylo: float
    turn_suspicion_risk: float


@dataclass(frozen=True, slots=True)
class DialogueLedgerPlane:
    """Plane 4: Idempotent append-only ledger plus bounded recent dialogue context window."""

    turns: tuple[TurnTranscriptRecord, ...] = ()
    evaluations: tuple[EvaluatedTurnRecord, ...] = ()
    socratic_probes: tuple[SocraticProbeState, ...] = ()
    prompt_hashes: tuple[PromptHashAuditRecord, ...] = ()
    telemetry_snapshots: tuple[ClientTelemetryRecord, ...] = ()
    integrity_flags: tuple[IntegrityFlag, ...] = ()
    recent_dialogue_window: tuple[TurnTranscriptRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class AdaptiveRoutingPlane:
    """Plane 3: Mutable epistemic mastery state, Socratic counters, and active prompt."""

    phase: InterviewGraphPhase
    active_competency_id: UUID
    current_difficulty: DifficultyLevel
    questions_asked_in_active_competency: int
    probes_used_in_active_competency: int
    mastery_by_competency: Mapping[UUID, float]
    rolling_authenticity_index: float
    total_tokens_consumed: int
    completed_turns: int
    active_prompt_text: str
    active_prompt_hash: PromptHash
    last_routing_action: RoutingAction | None = None
    last_routing_rule_id: str | None = None
    synthesis_summary: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mastery_by_competency, MappingProxyType):
            object.__setattr__(
                self,
                "mastery_by_competency",
                MappingProxyType(dict(self.mastery_by_competency)),
            )


@dataclass(frozen=True, slots=True)
class InterviewGraphState:
    """Root immutable 4-Plane ADR-005 state persisted per checkpoint version."""

    metadata: SessionMetadataPlane
    rubric: RubricContextPlane
    routing: AdaptiveRoutingPlane
    ledger: DialogueLedgerPlane
    checkpoint_version: str
    fencing_token: int

    def observations_for(
        self, competency_id: UUID
    ) -> tuple[tuple[DifficultyLevel, float, int], ...]:
        """Return ``(difficulty, Q_eff, probe_index)`` observations matching Application logic."""
        return tuple(
            (
                item.difficulty,
                item.evaluation.effective_quality_score(),
                item.probe_index,
            )
            for item in self.ledger.evaluations
            if item.competency_id == competency_id
        )

    def has_prompt_hash(self, prompt_hash: PromptHash) -> bool:
        """Return True if a prompt SHA-256 already appeared earlier in this session."""
        return any(
            entry.prompt_hash.sha256.value == prompt_hash.sha256.value
            for entry in self.ledger.prompt_hashes
        )


def derive_checkpoint_version(
    *,
    session_id: UUID,
    fencing_token: int,
    completed_turns: int,
    prompt_hash: PromptHash,
) -> str:
    """Build deterministic short checkpoint token from monotonic fence and prompt digest."""
    raw = f"{session_id}:{fencing_token}:{completed_turns}:{prompt_hash.sha256.value}".encode()
    short_hex = sha256(raw).hexdigest()[:8]
    return f"ckpt-{fencing_token:04d}-t{completed_turns:02d}-{short_hex}"


class StateReducer:
    """Deterministic idempotent reducers preventing duplicate Plane 4 records on replay."""

    @staticmethod
    def append_turn(
        existing: Sequence[TurnTranscriptRecord],
        incoming: TurnTranscriptRecord,
        *,
        window_size: int = DEFAULT_RECENT_WINDOW_SIZE,
    ) -> tuple[tuple[TurnTranscriptRecord, ...], tuple[TurnTranscriptRecord, ...]]:
        """Append ``incoming`` keyed by ``turn_number`` and return bounded recent window."""
        by_turn: dict[int, TurnTranscriptRecord] = {item.turn_number: item for item in existing}
        by_turn[incoming.turn_number] = incoming
        ordered = tuple(by_turn[k] for k in sorted(by_turn))
        recent = ordered[-max(1, window_size) :]
        return ordered, recent

    @staticmethod
    def append_evaluation(
        existing: Sequence[EvaluatedTurnRecord],
        incoming: EvaluatedTurnRecord,
    ) -> tuple[EvaluatedTurnRecord, ...]:
        """Append ``incoming`` evaluation keyed idempotently by ``turn_number``."""
        by_turn: dict[int, EvaluatedTurnRecord] = {item.turn_number: item for item in existing}
        by_turn[incoming.turn_number] = incoming
        return tuple(by_turn[k] for k in sorted(by_turn))

    @staticmethod
    def append_probe(
        existing: Sequence[SocraticProbeState],
        incoming: SocraticProbeState,
    ) -> tuple[SocraticProbeState, ...]:
        """Append Socratic probe record keyed idempotently by ``(turn_number, probe_index)``."""
        by_key: dict[tuple[int, int], SocraticProbeState] = {
            (item.turn_number, item.probe_index): item for item in existing
        }
        by_key[(incoming.turn_number, incoming.probe_index)] = incoming
        return tuple(by_key[k] for k in sorted(by_key))

    @staticmethod
    def append_prompt_hash(
        existing: Sequence[PromptHashAuditRecord],
        incoming: PromptHashAuditRecord,
    ) -> tuple[PromptHashAuditRecord, ...]:
        """Append prompt hash audit record idempotently by ``(turn_number, role, sha256.value)``."""
        by_key: dict[tuple[int, str, str], PromptHashAuditRecord] = {
            (item.turn_number, item.role, item.prompt_hash.sha256.value): item
            for item in existing
        }
        by_key[(incoming.turn_number, incoming.role, incoming.prompt_hash.sha256.value)] = incoming
        return tuple(by_key[k] for k in sorted(by_key))

    @staticmethod
    def append_telemetry(
        existing: Sequence[ClientTelemetryRecord],
        incoming: ClientTelemetryRecord,
    ) -> tuple[ClientTelemetryRecord, ...]:
        """Append client telemetry snapshot idempotently by ``turn_number``."""
        by_turn: dict[int, ClientTelemetryRecord] = {item.turn_number: item for item in existing}
        by_turn[incoming.turn_number] = incoming
        return tuple(by_turn[k] for k in sorted(by_turn))

    @staticmethod
    def append_flags(
        existing: Sequence[IntegrityFlag],
        incoming: Sequence[IntegrityFlag],
    ) -> tuple[IntegrityFlag, ...]:
        """Merge integrity flags idempotently by ``(turn_number, flag_code)``."""
        by_key: dict[tuple[int, str], IntegrityFlag] = {
            (item.turn_number, item.flag_code): item for item in existing
        }
        for flag in incoming:
            by_key[(flag.turn_number, flag.flag_code)] = flag
        return tuple(by_key[k] for k in sorted(by_key))

    @classmethod
    def with_recorded_turn(
        cls,
        state: InterviewGraphState,
        *,
        turn: TurnTranscriptRecord,
        evaluation: EvaluatedTurnRecord,
        telemetry: ClientTelemetryRecord,
        next_routing: AdaptiveRoutingPlane,
        new_fencing_token: int,
        probe: SocraticProbeState | None = None,
        next_prompt_audit: PromptHashAuditRecord | None = None,
        flags: Sequence[IntegrityFlag] = (),
        window_size: int = DEFAULT_RECENT_WINDOW_SIZE,
    ) -> InterviewGraphState:
        """Apply atomic immutable state transition across Planes 3 and 4."""
        turns, recent_window = cls.append_turn(
            state.ledger.turns, turn, window_size=window_size
        )
        evaluations = cls.append_evaluation(state.ledger.evaluations, evaluation)
        probes = (
            cls.append_probe(state.ledger.socratic_probes, probe)
            if probe is not None
            else state.ledger.socratic_probes
        )
        prompt_hashes = (
            cls.append_prompt_hash(state.ledger.prompt_hashes, next_prompt_audit)
            if next_prompt_audit is not None
            else state.ledger.prompt_hashes
        )
        telemetry_records = cls.append_telemetry(state.ledger.telemetry_snapshots, telemetry)
        merged_flags = cls.append_flags(state.ledger.integrity_flags, flags)

        next_ledger = DialogueLedgerPlane(
            turns=turns,
            evaluations=evaluations,
            socratic_probes=probes,
            prompt_hashes=prompt_hashes,
            telemetry_snapshots=telemetry_records,
            integrity_flags=merged_flags,
            recent_dialogue_window=recent_window,
        )
        next_ckpt = derive_checkpoint_version(
            session_id=state.metadata.session_id,
            fencing_token=new_fencing_token,
            completed_turns=next_routing.completed_turns,
            prompt_hash=next_routing.active_prompt_hash,
        )
        return replace(
            state,
            routing=next_routing,
            ledger=next_ledger,
            checkpoint_version=next_ckpt,
            fencing_token=new_fencing_token,
        )


def build_initial_graph_state(
    *,
    session_id: UUID,
    tenant_id: TenantId,
    requisition_id: UUID,
    candidate_profile_id: UUID,
    rubric_version: VersionNumber,
    competency_targets: Sequence[CompetencyBriefingTargetDTO],
    candidate_project_highlights: Sequence[str],
    opening_question: str,
    opening_prompt_hash: PromptHash,
    opening_tokens_used: int,
    max_wall_clock_seconds: int = 1800,
    max_turns: int = 16,
    token_budget_limit: int = 45_000,
    max_probes_per_competency: int = 2,
    initial_difficulty: DifficultyLevel = DifficultyLevel.L3,
    fencing_token: int = 1,
    started_at: datetime | None = None,
) -> InterviewGraphState:
    """Create initial Plane 1-4 state anchored on the first prioritized competency."""
    targets_tuple = tuple(competency_targets)
    active_comp = targets_tuple[0]
    metadata = SessionMetadataPlane(
        session_id=session_id,
        tenant_id=tenant_id,
        requisition_id=requisition_id,
        candidate_profile_id=candidate_profile_id,
        rubric_version=rubric_version,
        started_at=started_at if started_at is not None else utc_now(),
        max_wall_clock_seconds=max_wall_clock_seconds,
        max_turns=max_turns,
        token_budget_limit=token_budget_limit,
        max_probes_per_competency=max_probes_per_competency,
    )
    rubric = RubricContextPlane(
        competency_queue=targets_tuple,
        candidate_project_highlights=tuple(candidate_project_highlights),
    )
    routing = AdaptiveRoutingPlane(
        phase=InterviewGraphPhase.QUESTION_GENERATED,
        active_competency_id=active_comp.competency_id,
        current_difficulty=initial_difficulty,
        questions_asked_in_active_competency=0,
        probes_used_in_active_competency=0,
        mastery_by_competency=MappingProxyType({c.competency_id: 0.0 for c in targets_tuple}),
        rolling_authenticity_index=1.0,
        total_tokens_consumed=opening_tokens_used,
        completed_turns=0,
        active_prompt_text=opening_question,
        active_prompt_hash=opening_prompt_hash,
    )
    ledger = DialogueLedgerPlane(
        prompt_hashes=(
            PromptHashAuditRecord(
                turn_number=0,
                role="INITIAL_QUESTION",
                prompt_hash=opening_prompt_hash,
            ),
        ),
    )
    ckpt = derive_checkpoint_version(
        session_id=session_id,
        fencing_token=fencing_token,
        completed_turns=0,
        prompt_hash=opening_prompt_hash,
    )
    return InterviewGraphState(
        metadata=metadata,
        rubric=rubric,
        routing=routing,
        ledger=ledger,
        checkpoint_version=ckpt,
        fencing_token=fencing_token,
    )
