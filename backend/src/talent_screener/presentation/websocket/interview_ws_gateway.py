"""ADR-007 Full-Duplex Stateful WebSocket Interview Protocol Gateway (/v1/interviews/{id}/ws)."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from talent_screener.application.common.dtos import FencingLeaseDTO
from talent_screener.application.common.exceptions import (
    GuardrailViolationError,
    UnauthorizedTenantAccessError,
)
from talent_screener.application.common.ports.outbound import DistributedLockPort
from talent_screener.application.live_interview.dtos import (
    ClientTelemetryPayloadDTO,
    ProcessCandidateTurnRequestDTO,
    TerminateInterviewEarlyRequestDTO,
)
from talent_screener.application.live_interview.ports.inbound import (
    ProcessCandidateTurnPort,
    RecordClientTelemetryPort,
    TerminateInterviewEarlyPort,
)
from talent_screener.application.live_interview.ports.outbound import (
    AudioSttPort,
    AudioTtsPort,
)
from talent_screener.domain.contexts.live_interview.value_objects import DifficultyLevel
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.presentation.middleware import (
    BearerTokenAuthenticator,
    ExceptionToProblemDetailsTranslator,
    PrincipalContext,
    RbacScope,
    RequestValidationError,
)
from talent_screener.presentation.schemas import FieldViolation, serialize_value
from talent_screener.presentation.schemas.payloads import (
    parse_bool,
    parse_float,
    parse_int,
    parse_record_telemetry,
)
from talent_screener.presentation.websocket.voice_sync import (
    BargeInStreamController,
    compute_adaptive_silence_threshold_ms,
)

_WS_ROUTE_RE = re.compile(r"^(?:/v1/interviews|/ws/v1/interviews)/(?P<session_id>[^/]+)(?:/ws)?$")


@dataclass(frozen=True, slots=True)
class WebSocketFrame:
    """Structured outbound/inbound protocol frame with monotonic server sequence counter."""

    event_type: str
    session_id: UUID
    seq: int
    turn_number: int
    payload: Mapping[str, object]
    trace_id: str
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "session_id": str(self.session_id),
            "seq": self.seq,
            "turn_number": self.turn_number,
            "payload": serialize_value(self.payload),
            "trace_id": self.trace_id,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(slots=True)
class _MutableSessionState:
    session_id: UUID
    tenant_id: TenantId
    current_turn_number: int
    active_question: str
    current_difficulty: DifficultyLevel
    checkpoint_version: str
    remaining_seconds: int
    tokens_consumed: int = 0
    is_completed: bool = False
    server_seq: int = 0
    history: list[WebSocketFrame] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SessionSyncSnapshot:
    """Read-only state sync snapshot exposed during handshake reconnection recovery."""

    session_id: UUID
    tenant_id: TenantId
    turn_number: int
    active_question: str
    current_difficulty: DifficultyLevel
    checkpoint_version: str
    remaining_seconds: int
    tokens_consumed: int
    is_completed: bool
    server_seq: int


@dataclass(slots=True)
class WebSocketConnectionContext:
    """Active full-duplex socket connection bound to one authenticated principal & fencing lease."""

    connection_id: UUID
    session_id: UUID
    principal: PrincipalContext
    lease: FencingLeaseDTO
    connected_at: datetime
    barge_in: BargeInStreamController = field(default_factory=BargeInStreamController)
    audio_chunks: list[bytes] = field(default_factory=list)
    partial_transcript: str = ""
    accumulated_silence_ms: int = 0
    last_vad_wpm: float = 125.0
    last_vad_entropy: float = 0.45


class WebSocketInterviewGateway:
    """Full-duplex stateful WebSocket protocol controller for live adaptive interviews."""

    def __init__(
        self,
        *,
        authenticator: BearerTokenAuthenticator,
        lock_port: DistributedLockPort,
        process_turn_use_case: ProcessCandidateTurnPort,
        record_telemetry_use_case: RecordClientTelemetryPort,
        terminate_interview_use_case: TerminateInterviewEarlyPort,
        stt_port: AudioSttPort,
        tts_port: AudioTtsPort,
        error_translator: ExceptionToProblemDetailsTranslator | None = None,
    ) -> None:
        self._auth = authenticator
        self._lock_port = lock_port
        self._process_turn = process_turn_use_case
        self._record_telemetry = record_telemetry_use_case
        self._terminate = terminate_interview_use_case
        self._stt_port = stt_port
        self._tts_port = tts_port
        self._translator = (
            error_translator
            if error_translator is not None
            else ExceptionToProblemDetailsTranslator()
        )
        self._sessions: dict[UUID, _MutableSessionState] = {}
        self._connections: dict[UUID, WebSocketConnectionContext] = {}
        self._active_conn_by_session: dict[UUID, UUID] = {}

    @staticmethod
    def match_ws_path(path: str) -> UUID | None:
        """Extract session_id UUID from ``/v1/interviews/{session_id}/ws`` or None."""
        cleaned = path.split("?", maxsplit=1)[0].rstrip("/")
        match = _WS_ROUTE_RE.fullmatch(cleaned)
        if match is None:
            return None
        return UUID(match.group("session_id"))

    def get_session_snapshot(self, session_id: UUID) -> SessionSyncSnapshot | None:
        """Return immutable session sync snapshot for test verification & observability."""
        st = self._sessions.get(session_id)
        if st is None:
            return None
        return SessionSyncSnapshot(
            session_id=st.session_id,
            tenant_id=st.tenant_id,
            turn_number=st.current_turn_number,
            active_question=st.active_question,
            current_difficulty=st.current_difficulty,
            checkpoint_version=st.checkpoint_version,
            remaining_seconds=st.remaining_seconds,
            tokens_consumed=st.tokens_consumed,
            is_completed=st.is_completed,
            server_seq=st.server_seq,
        )

    def _emit(
        self,
        session_state: _MutableSessionState,
        *,
        event_type: str,
        payload: Mapping[str, object],
        trace_id: str,
    ) -> WebSocketFrame:
        session_state.server_seq += 1
        frame = WebSocketFrame(
            event_type=event_type,
            session_id=session_state.session_id,
            seq=session_state.server_seq,
            turn_number=session_state.current_turn_number,
            payload=MappingProxyType(dict(payload)),
            trace_id=trace_id,
        )
        session_state.history.append(frame)
        return frame

    async def connect(
        self,
        *,
        session_id: UUID,
        authorization_header: str | None = None,
        ticket_token: str | None = None,
        last_server_seq: int | None = None,
        initial_question: str = "",
        current_difficulty: DifficultyLevel = DifficultyLevel.L3,
        checkpoint_version: str = "",
        remaining_seconds: int = 1800,
    ) -> tuple[WebSocketConnectionContext, tuple[WebSocketFrame, ...]]:
        """Authenticate handshake, acquire lease lock, and emit session.ready or state_sync."""
        principal = (
            self._auth.verify_raw_token(ticket_token)
            if ticket_token is not None
            else self._auth.authenticate(authorization_header)
        )
        self._auth.require_scopes(
            principal,
            (RbacScope.INTERVIEW_CANDIDATE, RbacScope.INTERVIEW_MANAGE),
            any_of=True,
        )

        existing = self._sessions.get(session_id)
        is_reconnect = existing is not None or last_server_seq is not None
        if existing is not None and existing.tenant_id != principal.tenant_id:
            raise UnauthorizedTenantAccessError(
                "Cross-tenant WebSocket session handshake forbidden.",
                code="WS_CROSS_TENANT_FORBIDDEN",
            )

        # Evict any lingering prior connection cleanly before reacquiring exclusive session lease
        prior_conn_id = self._active_conn_by_session.get(session_id)
        if prior_conn_id is not None:
            await self.disconnect(prior_conn_id)

        lease = await self._lock_port.acquire(
            tenant_id=principal.tenant_id,
            resource_key=f"ws_interview:{session_id}",
            ttl_seconds=30,
        )

        if existing is None:
            state = _MutableSessionState(
                session_id=session_id,
                tenant_id=principal.tenant_id,
                current_turn_number=1,
                active_question=initial_question,
                current_difficulty=current_difficulty,
                checkpoint_version=checkpoint_version,
                remaining_seconds=remaining_seconds,
            )
            self._sessions[session_id] = state
        else:
            state = existing
            if not state.active_question and initial_question:
                state.active_question = initial_question
            if not state.checkpoint_version and checkpoint_version:
                state.checkpoint_version = checkpoint_version

        conn = WebSocketConnectionContext(
            connection_id=generate_uuid7(),
            session_id=session_id,
            principal=principal,
            lease=lease,
            connected_at=utc_now(),
        )
        self._connections[conn.connection_id] = conn
        self._active_conn_by_session[session_id] = conn.connection_id

        trace_id = str(generate_uuid7())
        sync_payload: dict[str, object] = {
            "session_id": str(session_id),
            "tenant_id": str(principal.tenant_id.value),
            "turn_number": state.current_turn_number,
            "active_question": state.active_question,
            "difficulty": int(state.current_difficulty),
            "checkpoint_version": state.checkpoint_version,
            "remaining_seconds": state.remaining_seconds,
            "tokens_consumed": state.tokens_consumed,
            "is_completed": state.is_completed,
            "fencing_token": lease.fencing_token,
        }
        if is_reconnect:
            replay_after = last_server_seq if last_server_seq is not None else 0
            missed = [
                frame.to_dict() for frame in state.history if frame.seq > replay_after
            ]
            sync_payload["reconnected_after_seq"] = replay_after
            sync_payload["missed_events"] = missed
            frame = self._emit(
                state,
                event_type="session.state_sync",
                payload=sync_payload,
                trace_id=trace_id,
            )
        else:
            frame = self._emit(
                state,
                event_type="session.ready",
                payload=sync_payload,
                trace_id=trace_id,
            )
        return conn, (frame,)

    async def disconnect(self, connection_id: UUID) -> None:
        """Close socket connection cleanly while preserving recoverable session checkpoint."""
        conn = self._connections.pop(connection_id, None)
        if conn is None:
            return
        if conn.barge_in.is_streaming:
            conn.barge_in.trigger_barge_in(reason="CONNECTION_DISCONNECTED")
        if self._active_conn_by_session.get(conn.session_id) == connection_id:
            del self._active_conn_by_session[conn.session_id]
        await self._lock_port.release(lease=conn.lease)

    async def receive_frame(
        self,
        connection_id: UUID,
        raw_frame: Mapping[str, object] | str,
    ) -> tuple[WebSocketFrame, ...]:
        """Process one inbound client WebSocket event and return ordered outbound frames."""
        conn = self._connections.get(connection_id)
        if conn is None:
            raise RequestValidationError(
                f"Unknown or disconnected WebSocket connection {connection_id}.",
                code="WS_CONNECTION_NOT_FOUND",
            )
        state = self._sessions[conn.session_id]
        trace_id = str(generate_uuid7())

        try:
            frame_obj: Mapping[str, object]
            if isinstance(raw_frame, str):
                parsed = json.loads(raw_frame)
                if not isinstance(parsed, Mapping):
                    raise RequestValidationError(
                        "WebSocket frame must decode to a JSON object.",
                        code="INVALID_WS_FRAME",
                    )
                frame_obj = parsed
            else:
                frame_obj = raw_frame

            event_type = str(frame_obj.get("event_type", "")).strip()
            raw_payload = frame_obj.get("payload", {})
            if not isinstance(raw_payload, Mapping):
                raise RequestValidationError(
                    "WebSocket frame 'payload' must be an object.",
                    code="INVALID_WS_PAYLOAD",
                )

            if event_type == "ping":
                return (
                    self._emit(
                        state,
                        event_type="pong",
                        payload={
                            "client_timestamp": raw_payload.get("client_timestamp", ""),
                            "server_timestamp": utc_now().isoformat(),
                        },
                        trace_id=trace_id,
                    ),
                )

            if event_type == "session.join":
                return self._handle_session_join(state, raw_payload, trace_id=trace_id)

            if event_type == "candidate.barge_in":
                return self._handle_barge_in(conn, state, raw_payload, trace_id=trace_id)

            if event_type == "telemetry.snapshot":
                return await self._handle_telemetry(conn, state, raw_payload, trace_id=trace_id)

            if event_type == "session.terminate":
                return await self._handle_terminate(conn, state, raw_payload, trace_id=trace_id)

            if event_type == "audio.chunk":
                return await self._handle_audio_chunk(conn, state, raw_payload, trace_id=trace_id)

            if event_type == "candidate.utterance":
                return await self._handle_candidate_utterance(
                    conn, state, raw_payload, trace_id=trace_id
                )

            raise RequestValidationError(
                f"Unsupported WebSocket event_type {event_type!r}.",
                code="UNSUPPORTED_WS_EVENT",
                errors=(
                    FieldViolation(
                        field_path="event_type",
                        constraint="enum",
                        reason=f"Unknown event {event_type!r}",
                    ),
                ),
            )
        except GuardrailViolationError as exc:
            warning = self._emit(
                state,
                event_type="integrity.warning",
                payload={
                    "reason_code": exc.code,
                    "severity": "CRITICAL",
                    "turn_number": state.current_turn_number,
                },
                trace_id=trace_id,
            )
            problem = self._translator.translate(
                exc, instance=f"/v1/interviews/{state.session_id}/ws", trace_id=trace_id
            )
            err = self._emit(
                state,
                event_type="error.problem",
                payload=problem.to_dict(),
                trace_id=trace_id,
            )
            return (warning, err)
        except Exception as exc:
            problem = self._translator.translate(
                exc, instance=f"/v1/interviews/{state.session_id}/ws", trace_id=trace_id
            )
            return (
                self._emit(
                    state,
                    event_type="error.problem",
                    payload=problem.to_dict(),
                    trace_id=trace_id,
                ),
            )

    def pull_next_tts_chunk_frame(self, connection_id: UUID) -> WebSocketFrame | None:
        """Advance deferred TTS audio stream by one chunk unless cancelled by barge-in."""
        conn = self._connections.get(connection_id)
        if conn is None:
            return None
        chunk = conn.barge_in.pull_next_chunk()
        if chunk is None:
            return None
        state = self._sessions[conn.session_id]
        return self._emit(
            state,
            event_type="tts.audio_chunk",
            payload={
                "turn_number": chunk.turn_number,
                "chunk_index": chunk.chunk_index,
                "duration_ms": chunk.duration_ms,
                "is_final_chunk": chunk.is_final_chunk,
                "audio_base64": base64.b64encode(chunk.audio_bytes).decode("ascii"),
            },
            trace_id=str(generate_uuid7()),
        )

    def _handle_session_join(
        self,
        state: _MutableSessionState,
        payload: Mapping[str, object],
        *,
        trace_id: str,
    ) -> tuple[WebSocketFrame, ...]:
        if isinstance(payload.get("active_question"), str) and payload["active_question"]:
            state.active_question = str(payload["active_question"])
        if isinstance(payload.get("checkpoint_version"), str) and payload["checkpoint_version"]:
            state.checkpoint_version = str(payload["checkpoint_version"])
        if "difficulty" in payload:
            parsed_diff = parse_int(payload, "difficulty", minimum=1, maximum=5)
            state.current_difficulty = DifficultyLevel.from_int(parsed_diff)
        if "remaining_seconds" in payload:
            state.remaining_seconds = parse_int(payload, "remaining_seconds", minimum=0)

        is_resumed = state.current_turn_number > 1 or bool(state.tokens_consumed)
        event_name = "session.state_sync" if is_resumed else "session.ready"
        return (
            self._emit(
                state,
                event_type=event_name,
                payload={
                    "session_id": str(state.session_id),
                    "turn_number": state.current_turn_number,
                    "active_question": state.active_question,
                    "difficulty": int(state.current_difficulty),
                    "checkpoint_version": state.checkpoint_version,
                    "remaining_seconds": state.remaining_seconds,
                    "tokens_consumed": state.tokens_consumed,
                },
                trace_id=trace_id,
            ),
        )

    def _handle_barge_in(
        self,
        conn: WebSocketConnectionContext,
        state: _MutableSessionState,
        payload: Mapping[str, object],
        *,
        trace_id: str,
    ) -> tuple[WebSocketFrame, ...]:
        raw_ms = payload.get("interrupted_at_ms")
        explicit_ms = raw_ms if isinstance(raw_ms, int) and not isinstance(raw_ms, bool) else None
        receipt = conn.barge_in.trigger_barge_in(interrupted_at_ms=explicit_ms)
        return (
            self._emit(
                state,
                event_type="tts.flush_interrupt",
                payload={
                    "interrupted_turn": receipt.turn_number,
                    "interrupted_at_ms": receipt.interrupted_at_ms,
                    "delivered_chunks": receipt.delivered_chunks,
                    "cancelled_remaining_chunks": receipt.cancelled_remaining_chunks,
                    "reason": receipt.reason,
                },
                trace_id=trace_id,
            ),
        )

    async def _handle_telemetry(
        self,
        conn: WebSocketConnectionContext,
        state: _MutableSessionState,
        payload: Mapping[str, object],
        *,
        trace_id: str,
    ) -> tuple[WebSocketFrame, ...]:
        normalized = dict(payload)
        normalized.setdefault("turn_number", state.current_turn_number)
        cmd = parse_record_telemetry(
            normalized, tenant_id=conn.principal.tenant_id, session_id=state.session_id
        )
        res = await self._record_telemetry.execute(cmd)
        if res.flags_recorded > 0:
            return (
                self._emit(
                    state,
                    event_type="integrity.warning",
                    payload={
                        "turn_number": res.turn_number,
                        "flags_recorded": res.flags_recorded,
                        "flag_codes": list(res.integrity_flag_codes),
                    },
                    trace_id=trace_id,
                ),
            )
        return ()

    async def _handle_terminate(
        self,
        conn: WebSocketConnectionContext,
        state: _MutableSessionState,
        payload: Mapping[str, object],
        *,
        trace_id: str,
    ) -> tuple[WebSocketFrame, ...]:
        reason = str(payload.get("reason", "Session terminated by WebSocket client")).strip()
        outcome = await self._terminate.execute(
            TerminateInterviewEarlyRequestDTO(
                tenant_id=conn.principal.tenant_id,
                session_id=state.session_id,
                reason=reason or "Terminated cleanly",
            )
        )
        state.is_completed = True
        return (
            self._emit(
                state,
                event_type="session.completed",
                payload={
                    "session_id": str(outcome.session_id),
                    "status": outcome.status.value,
                    "total_turns": outcome.total_turns,
                    "reason": outcome.reason,
                },
                trace_id=trace_id,
            ),
        )

    async def _handle_audio_chunk(
        self,
        conn: WebSocketConnectionContext,
        state: _MutableSessionState,
        payload: Mapping[str, object],
        *,
        trace_id: str,
    ) -> tuple[WebSocketFrame, ...]:
        emitted: list[WebSocketFrame] = []
        raw_rms = parse_float(payload, "rms_energy", default=0.0)
        interrupt = conn.barge_in.process_vad_energy(rms_energy=raw_rms)
        if interrupt is not None:
            emitted.append(
                self._emit(
                    state,
                    event_type="tts.flush_interrupt",
                    payload={
                        "interrupted_turn": interrupt.turn_number,
                        "interrupted_at_ms": interrupt.interrupted_at_ms,
                        "delivered_chunks": interrupt.delivered_chunks,
                        "cancelled_remaining_chunks": interrupt.cancelled_remaining_chunks,
                        "reason": interrupt.reason,
                    },
                    trace_id=trace_id,
                )
            )

        audio_bytes = self._decode_audio_payload(payload)
        if audio_bytes:
            conn.audio_chunks.append(audio_bytes)
            transcript = await self._stt_port.transcribe_audio(
                tenant_id=conn.principal.tenant_id,
                session_id=state.session_id,
                audio_bytes=audio_bytes,
            )
            if transcript.transcript_text:
                conn.partial_transcript = (
                    f"{conn.partial_transcript} {transcript.transcript_text}".strip()
                )
                conn.last_vad_wpm = transcript.speech_cadence_wpm
                conn.last_vad_entropy = transcript.pause_entropy

        silence_ms = parse_int(payload, "silence_ms", default=0, minimum=0)
        conn.accumulated_silence_ms = silence_ms
        threshold_ms = compute_adaptive_silence_threshold_ms(
            difficulty=state.current_difficulty,
            trailing_transcript=conn.partial_transcript,
        )
        emitted.append(
            self._emit(
                state,
                event_type="stt.partial_transcript",
                payload={
                    "turn_number": state.current_turn_number,
                    "partial_transcript": conn.partial_transcript,
                    "silence_ms": silence_ms,
                    "adaptive_silence_threshold_ms": threshold_ms,
                    "end_of_turn_ready": silence_ms >= threshold_ms,
                },
                trace_id=trace_id,
            )
        )

        is_final = parse_bool(payload, "is_final", default=False) or (
            bool(conn.partial_transcript) and silence_ms >= threshold_ms
        )
        if is_final and conn.partial_transcript:
            utterance_text = conn.partial_transcript
            conn.partial_transcript = ""
            conn.audio_chunks.clear()
            conn.accumulated_silence_ms = 0
            turn_frames = await self._handle_candidate_utterance(
                conn,
                state,
                {
                    "utterance": utterance_text,
                    "remaining_seconds": parse_int(
                        payload, "remaining_seconds", default=state.remaining_seconds, minimum=0
                    ),
                    "synthesize_voice": parse_bool(payload, "synthesize_voice", default=True),
                    "speech_cadence_wpm": conn.last_vad_wpm,
                    "pause_entropy": conn.last_vad_entropy,
                },
                trace_id=trace_id,
            )
            emitted.extend(turn_frames)

        return tuple(emitted)

    async def _handle_candidate_utterance(
        self,
        conn: WebSocketConnectionContext,
        state: _MutableSessionState,
        payload: Mapping[str, object],
        *,
        trace_id: str,
    ) -> tuple[WebSocketFrame, ...]:
        utterance = str(payload.get("utterance", "")).strip()
        if not utterance and not payload.get("audio_base64"):
            raise RequestValidationError(
                "candidate.utterance requires non-empty 'utterance' or 'audio_base64'.",
                code="EMPTY_CANDIDATE_UTTERANCE",
            )

        checkpoint = str(payload.get("checkpoint_version", state.checkpoint_version))
        turn_num = parse_int(
            payload, "turn_number", default=state.current_turn_number, minimum=1
        )
        remaining_sec = parse_int(
            payload, "remaining_seconds", default=state.remaining_seconds, minimum=0
        )
        synthesize_voice = parse_bool(payload, "synthesize_voice", default=True)
        defer_tts = parse_bool(payload, "defer_tts_remaining", default=False)

        telemetry = ClientTelemetryPayloadDTO(
            focus_loss_count=parse_int(payload, "focus_loss_count", default=0, minimum=0),
            focus_loss_duration_ms=parse_int(
                payload, "focus_loss_duration_ms", default=0, minimum=0
            ),
            external_paste_detected=parse_bool(
                payload, "external_paste_detected", default=False
            ),
            paste_char_count=parse_int(payload, "paste_char_count", default=0, minimum=0),
            keystroke_anomaly_detected=parse_bool(
                payload, "keystroke_anomaly_detected", default=False
            ),
            response_latency_ms=parse_int(
                payload, "response_latency_ms", default=1100, minimum=0
            ),
            speech_cadence_wpm=parse_float(
                payload, "speech_cadence_wpm", default=conn.last_vad_wpm
            ),
            pause_entropy=parse_float(
                payload, "pause_entropy", default=conn.last_vad_entropy
            ),
        )

        emitted: list[WebSocketFrame] = [
            self._emit(
                state,
                event_type="interviewer.thinking",
                payload={
                    "turn_number": turn_num,
                    "stage": "EVALUATING_AND_ROUTING",
                },
                trace_id=trace_id,
            )
        ]

        result = await self._process_turn.execute(
            ProcessCandidateTurnRequestDTO(
                tenant_id=conn.principal.tenant_id,
                session_id=state.session_id,
                turn_number=turn_num,
                checkpoint_version=checkpoint,
                candidate_utterance=utterance,
                remaining_seconds=remaining_sec,
                telemetry=telemetry,
                correlation_id=trace_id,
                synthesize_voice=synthesize_voice,
            )
        )

        state.checkpoint_version = result.updated_checkpoint_version
        state.current_difficulty = result.next_difficulty
        state.remaining_seconds = remaining_sec
        state.tokens_consumed = result.tokens_consumed

        emitted.append(
            self._emit(
                state,
                event_type="turn.evaluation_ack",
                payload={
                    "turn_number": result.turn_number,
                    "effective_quality_score": result.effective_quality_score,
                    "routing_action": result.routing_action.value,
                    "next_difficulty": int(result.next_difficulty),
                    "tokens_consumed": result.tokens_consumed,
                    "updated_checkpoint_version": result.updated_checkpoint_version,
                },
                trace_id=trace_id,
            )
        )

        if result.is_completed:
            state.is_completed = True
            emitted.append(
                self._emit(
                    state,
                    event_type="session.completed",
                    payload={
                        "session_id": str(state.session_id),
                        "status": result.session_status.value,
                        "total_turns": result.turn_number,
                        "closing_message": result.next_question,
                        "tokens_consumed": result.tokens_consumed,
                    },
                    trace_id=trace_id,
                )
            )
            return tuple(emitted)

        state.current_turn_number = result.turn_number + 1
        state.active_question = result.next_question
        emitted.append(
            self._emit(
                state,
                event_type="interviewer.question",
                payload={
                    "turn_number": state.current_turn_number,
                    "question_text": result.next_question,
                    "difficulty": int(result.next_difficulty),
                    "routing_action": result.routing_action.value,
                    "checkpoint_version": result.updated_checkpoint_version,
                },
                trace_id=trace_id,
            )
        )

        if synthesize_voice:
            audio_out = result.synthesized_audio or await self._tts_port.synthesize_speech(
                tenant_id=conn.principal.tenant_id,
                session_id=state.session_id,
                text=result.next_question,
            )
            conn.barge_in.begin_stream(
                turn_number=state.current_turn_number,
                audio_bytes=audio_out,
                chunk_size_bytes=48,
                chunk_duration_ms=40,
            )
            chunks_to_emit = (
                (conn.barge_in.pull_next_chunk(),)
                if defer_tts
                else conn.barge_in.pull_remaining_chunks()
            )
            for chunk in chunks_to_emit:
                if chunk is None:
                    continue
                emitted.append(
                    self._emit(
                        state,
                        event_type="tts.audio_chunk",
                        payload={
                            "turn_number": chunk.turn_number,
                            "chunk_index": chunk.chunk_index,
                            "duration_ms": chunk.duration_ms,
                            "is_final_chunk": chunk.is_final_chunk,
                            "audio_base64": base64.b64encode(chunk.audio_bytes).decode("ascii"),
                        },
                        trace_id=trace_id,
                    )
                )

        return tuple(emitted)

    @staticmethod
    def _decode_audio_payload(payload: Mapping[str, object]) -> bytes:
        raw_text = payload.get("text_chunk")
        if isinstance(raw_text, str) and raw_text.strip():
            return f"TEXT:{raw_text.strip()}".encode()
        raw_b64 = payload.get("audio_base64")
        if isinstance(raw_b64, str) and raw_b64.strip():
            try:
                return base64.b64decode(raw_b64.strip(), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise RequestValidationError(
                    "Invalid base64 audio payload.", code="INVALID_AUDIO_BASE64"
                ) from exc
        return b""
