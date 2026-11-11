"""Live Interview Bounded Context Entities."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    IntegrityFlag,
    PromptHash,
    ResponseLatencyMetric,
    TurnEvaluationScore,
)
from talent_screener.domain.shared.base import Entity
from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    require_int_range,
    require_non_empty_str,
    require_non_nil_uuid,
    require_utc_datetime,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now


class SocraticProbe(Entity[UUID]):
    """Child entity representing a targeted Socratic follow-up probe (index 1 or 2)."""

    __slots__ = (
        "_competency_id",
        "_probe_index",
        "_probe_prompt",
        "_prompt_hash",
        "_strategy",
        "_target_concept",
    )

    _VALID_STRATEGIES = frozenset(
        {
            "CLARIFY_VAGUE_CLAIM",
            "SHALLOW_BUZZWORD_PROBE",
            "UNSTATED_TRADEOFF_PROBE",
            "AUTHENTICITY_VERIFICATION_PROBE",
        }
    )

    def __init__(
        self,
        *,
        competency_id: UUID,
        probe_index: int,
        strategy: str,
        target_concept: str,
        probe_prompt: str,
        prompt_hash: PromptHash,
        probe_id: UUID | None = None,
    ) -> None:
        resolved_id = probe_id if probe_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "probe_id", code="INVALID_PROBE_ID")
        require_non_nil_uuid(competency_id, "competency_id", code="INVALID_COMPETENCY_ID")
        super().__init__(resolved_id)

        self._competency_id: UUID = competency_id
        self._probe_index: int = require_int_range(
            probe_index,
            "probe_index",
            1,
            2,
            code="SOCRATIC_PROBE_LIMIT_EXCEEDED",
        )
        cleaned_strategy = require_non_empty_str(
            strategy, "strategy", max_len=64, code="INVALID_PROBE_STRATEGY"
        ).upper()
        if cleaned_strategy not in self._VALID_STRATEGIES:
            raise InvariantViolationError(
                f"Unsupported SocraticProbe strategy: {cleaned_strategy!r}.",
                code="INVALID_PROBE_STRATEGY",
            )
        self._strategy: str = cleaned_strategy
        self._target_concept: str = require_non_empty_str(
            target_concept, "target_concept", max_len=160, code="INVALID_TARGET_CONCEPT"
        )
        self._probe_prompt: str = require_non_empty_str(
            probe_prompt, "probe_prompt", max_len=2000, code="INVALID_PROBE_PROMPT"
        )
        if not isinstance(prompt_hash, PromptHash):
            raise InvariantViolationError(
                "prompt_hash must be a PromptHash.",
                code="INVALID_PROMPT_HASH",
            )
        self._prompt_hash: PromptHash = prompt_hash

    @property
    def competency_id(self) -> UUID:
        return self._competency_id

    @property
    def probe_index(self) -> int:
        return self._probe_index

    @property
    def strategy(self) -> str:
        return self._strategy

    @property
    def target_concept(self) -> str:
        return self._target_concept

    @property
    def probe_prompt(self) -> str:
        return self._probe_prompt

    @property
    def prompt_hash(self) -> PromptHash:
        return self._prompt_hash


class TelemetrySnapshot(Entity[UUID]):
    """Behavioral and cadence telemetry snapshot recorded during a turn."""

    __slots__ = (
        "_captured_at",
        "_external_paste_detected",
        "_focus_loss_count",
        "_focus_loss_duration_ms",
        "_keystroke_anomaly_detected",
        "_paste_char_count",
        "_response_latency",
        "_turn_number",
    )

    def __init__(
        self,
        *,
        turn_number: int,
        focus_loss_count: int = 0,
        focus_loss_duration_ms: int = 0,
        external_paste_detected: bool = False,
        paste_char_count: int = 0,
        keystroke_anomaly_detected: bool = False,
        response_latency: ResponseLatencyMetric | None = None,
        captured_at: datetime | None = None,
        snapshot_id: UUID | None = None,
    ) -> None:
        resolved_id = snapshot_id if snapshot_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "snapshot_id", code="INVALID_TELEMETRY_ID")
        super().__init__(resolved_id)

        self._turn_number: int = require_int_range(
            turn_number, "turn_number", 1, code="INVALID_TURN_NUMBER"
        )
        self._focus_loss_count: int = require_int_range(
            focus_loss_count, "focus_loss_count", 0, code="INVALID_TELEMETRY_METRIC"
        )
        self._focus_loss_duration_ms: int = require_int_range(
            focus_loss_duration_ms,
            "focus_loss_duration_ms",
            0,
            code="INVALID_TELEMETRY_METRIC",
        )
        self._paste_char_count: int = require_int_range(
            paste_char_count, "paste_char_count", 0, code="INVALID_TELEMETRY_METRIC"
        )
        if not isinstance(external_paste_detected, bool) or not isinstance(
            keystroke_anomaly_detected, bool
        ):
            raise InvariantViolationError(
                "Telemetry flags must be booleans.",
                code="INVALID_TELEMETRY_METRIC",
            )
        self._external_paste_detected: bool = external_paste_detected
        self._keystroke_anomaly_detected: bool = keystroke_anomaly_detected
        self._response_latency: ResponseLatencyMetric = (
            response_latency
            if response_latency is not None
            else ResponseLatencyMetric(ttft_ms=0, total_duration_ms=0)
        )
        self._captured_at: datetime = (
            require_utc_datetime(captured_at, "captured_at", code="INVALID_UTC_DATETIME")
            if captured_at is not None
            else utc_now()
        )

    @property
    def turn_number(self) -> int:
        return self._turn_number

    @property
    def focus_loss_count(self) -> int:
        return self._focus_loss_count

    @property
    def focus_loss_duration_ms(self) -> int:
        return self._focus_loss_duration_ms

    @property
    def external_paste_detected(self) -> bool:
        return self._external_paste_detected

    @property
    def paste_char_count(self) -> int:
        return self._paste_char_count

    @property
    def keystroke_anomaly_detected(self) -> bool:
        return self._keystroke_anomaly_detected

    @property
    def response_latency(self) -> ResponseLatencyMetric:
        return self._response_latency

    @property
    def captured_at(self) -> datetime:
        return self._captured_at


class AdaptiveTurn(Entity[UUID]):
    """Immutable-once-recorded conversational exchange inside an InterviewSession."""

    __slots__ = (
        "_candidate_response",
        "_competency_id",
        "_difficulty",
        "_evaluation",
        "_integrity_flags",
        "_latency_metric",
        "_prompt_hash",
        "_question_prompt",
        "_socratic_probe",
        "_tokens_used",
        "_turn_number",
    )

    def __init__(
        self,
        *,
        turn_number: int,
        competency_id: UUID,
        difficulty: DifficultyLevel,
        question_prompt: str,
        prompt_hash: PromptHash,
        candidate_response: str,
        evaluation: TurnEvaluationScore,
        tokens_used: int = 0,
        socratic_probe: SocraticProbe | None = None,
        latency_metric: ResponseLatencyMetric | None = None,
        integrity_flags: tuple[IntegrityFlag, ...] = (),
        turn_id: UUID | None = None,
    ) -> None:
        resolved_id = turn_id if turn_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "turn_id", code="INVALID_TURN_ID")
        require_non_nil_uuid(competency_id, "competency_id", code="INVALID_COMPETENCY_ID")
        super().__init__(resolved_id)

        self._turn_number: int = require_int_range(
            turn_number, "turn_number", 1, code="INVALID_TURN_NUMBER"
        )
        self._competency_id: UUID = competency_id
        self._difficulty: DifficultyLevel = DifficultyLevel.from_int(int(difficulty))
        self._question_prompt: str = require_non_empty_str(
            question_prompt, "question_prompt", max_len=2000, code="INVALID_QUESTION_PROMPT"
        )
        if not isinstance(prompt_hash, PromptHash):
            raise InvariantViolationError(
                "prompt_hash must be a PromptHash.",
                code="INVALID_PROMPT_HASH",
            )
        self._prompt_hash: PromptHash = prompt_hash
        self._candidate_response: str = require_non_empty_str(
            candidate_response,
            "candidate_response",
            max_len=8000,
            code="INVALID_CANDIDATE_RESPONSE",
        )
        if not isinstance(evaluation, TurnEvaluationScore):
            raise InvariantViolationError(
                "evaluation must be a TurnEvaluationScore.",
                code="INVALID_TURN_SCORE",
            )
        self._evaluation: TurnEvaluationScore = evaluation
        self._tokens_used: int = require_int_range(
            tokens_used, "tokens_used", 0, code="INVALID_TOKEN_COUNT"
        )
        self._socratic_probe: SocraticProbe | None = socratic_probe
        self._latency_metric: ResponseLatencyMetric | None = latency_metric
        self._integrity_flags: tuple[IntegrityFlag, ...] = tuple(integrity_flags)

    @property
    def turn_number(self) -> int:
        return self._turn_number

    @property
    def competency_id(self) -> UUID:
        return self._competency_id

    @property
    def difficulty(self) -> DifficultyLevel:
        return self._difficulty

    @property
    def question_prompt(self) -> str:
        return self._question_prompt

    @property
    def prompt_hash(self) -> PromptHash:
        return self._prompt_hash

    @property
    def candidate_response(self) -> str:
        return self._candidate_response

    @property
    def evaluation(self) -> TurnEvaluationScore:
        return self._evaluation

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def socratic_probe(self) -> SocraticProbe | None:
        return self._socratic_probe

    @property
    def latency_metric(self) -> ResponseLatencyMetric | None:
        return self._latency_metric

    @property
    def integrity_flags(self) -> tuple[IntegrityFlag, ...]:
        return self._integrity_flags
