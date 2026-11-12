"""ADR-009 Two-Tier (L1 Hot Cache + L2 Durable SQL) Fenced LangGraph Checkpointer."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID

from talent_screener.application.common.exceptions import (
    ExternalAdapterFailureError,
    ResourceConflictError,
    UnauthorizedTenantAccessError,
)
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
from talent_screener.domain.shared.value_objects import (
    ConfidenceScore,
    SHA256Hash,
    TenantId,
    VersionNumber,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview.checkpointer import (
    CheckpointSnapshotRecord,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview.state import (
    AdaptiveRoutingPlane,
    ClientTelemetryRecord,
    DialogueLedgerPlane,
    EvaluatedTurnRecord,
    InterviewGraphPhase,
    InterviewGraphState,
    PromptHashAuditRecord,
    RubricContextPlane,
    SessionMetadataPlane,
    SocraticProbeState,
    TurnTranscriptRecord,
)
from talent_screener.infrastructure.persistence.relational.schema import SqlDatabaseManager


def _ser_prompt_hash(ph: PromptHash) -> dict[str, str]:
    return {
        "sha256": ph.sha256.value,
        "template_version": ph.template_version,
    }


def _deser_prompt_hash(data: dict[str, Any]) -> PromptHash:
    return PromptHash(
        sha256=SHA256Hash(str(data["sha256"])),
        template_version=str(data["template_version"]),
    )


def _ser_turn_record(item: TurnTranscriptRecord) -> dict[str, object]:
    return {
        "turn_number": item.turn_number,
        "competency_id": str(item.competency_id),
        "question_prompt": item.question_prompt,
        "candidate_response": item.candidate_response,
        "difficulty": int(item.difficulty),
        "is_socratic_probe": item.is_socratic_probe,
        "probe_index": item.probe_index,
        "recorded_at": item.recorded_at.isoformat(),
    }


def _deser_turn_record(data: dict[str, Any]) -> TurnTranscriptRecord:
    return TurnTranscriptRecord(
        turn_number=int(data["turn_number"]),
        competency_id=UUID(str(data["competency_id"])),
        question_prompt=str(data["question_prompt"]),
        candidate_response=str(data["candidate_response"]),
        difficulty=DifficultyLevel.from_int(int(data["difficulty"])),
        is_socratic_probe=bool(data["is_socratic_probe"]),
        probe_index=int(data["probe_index"]),
        recorded_at=datetime.fromisoformat(str(data["recorded_at"])),
    )


class InterviewStateSerializer:
    """Canonical JSON + SHA-256 serializer/deserializer for 4-Plane InterviewGraphState."""

    @staticmethod
    def serialize(state: InterviewGraphState) -> tuple[str, str]:
        """Serialize InterviewGraphState and compute (canonical_json, sha256_hex)."""
        meta = state.metadata
        rubric = state.rubric
        routing = state.routing
        ledger = state.ledger

        payload: dict[str, object] = {
            "checkpoint_version": state.checkpoint_version,
            "fencing_token": state.fencing_token,
            "metadata": {
                "session_id": str(meta.session_id),
                "tenant_id": str(meta.tenant_id.value),
                "requisition_id": str(meta.requisition_id),
                "candidate_profile_id": str(meta.candidate_profile_id),
                "rubric_version": int(meta.rubric_version),
                "started_at": meta.started_at.isoformat(),
                "max_wall_clock_seconds": meta.max_wall_clock_seconds,
                "max_turns": meta.max_turns,
                "token_budget_limit": meta.token_budget_limit,
                "max_probes_per_competency": meta.max_probes_per_competency,
            },
            "rubric": {
                "competency_queue": [
                    {
                        "competency_id": str(c.competency_id),
                        "canonical_skill_id": c.canonical_skill_id,
                        "weight": c.weight,
                        "target_depth": c.target_depth,
                        "is_gatekeeper": c.is_gatekeeper,
                        "ideal_anchor_descriptor": c.ideal_anchor_descriptor,
                        "probe_priority": c.probe_priority,
                    }
                    for c in rubric.competency_queue
                ],
                "candidate_project_highlights": list(rubric.candidate_project_highlights),
            },
            "routing": {
                "phase": routing.phase.value,
                "active_competency_id": str(routing.active_competency_id),
                "current_difficulty": int(routing.current_difficulty),
                "questions_asked_in_active_competency": (
                    routing.questions_asked_in_active_competency
                ),
                "probes_used_in_active_competency": routing.probes_used_in_active_competency,
                "mastery_by_competency": {
                    str(k): v for k, v in routing.mastery_by_competency.items()
                },
                "rolling_authenticity_index": routing.rolling_authenticity_index,
                "total_tokens_consumed": routing.total_tokens_consumed,
                "completed_turns": routing.completed_turns,
                "active_prompt_text": routing.active_prompt_text,
                "active_prompt_hash": _ser_prompt_hash(routing.active_prompt_hash),
                "last_routing_action": (
                    routing.last_routing_action.value
                    if routing.last_routing_action is not None
                    else None
                ),
                "last_routing_rule_id": routing.last_routing_rule_id,
                "synthesis_summary": routing.synthesis_summary,
            },
            "ledger": {
                "turns": [_ser_turn_record(t) for t in ledger.turns],
                "recent_dialogue_window": [
                    _ser_turn_record(t) for t in ledger.recent_dialogue_window
                ],
                "evaluations": [
                    {
                        "turn_number": ev.turn_number,
                        "competency_id": str(ev.competency_id),
                        "difficulty": int(ev.difficulty),
                        "evaluation": {
                            "technical_accuracy": ev.evaluation.technical_accuracy,
                            "depth_of_understanding": ev.evaluation.depth_of_understanding,
                            "tradeoff_awareness": ev.evaluation.tradeoff_awareness,
                            "contextual_specificity": ev.evaluation.contextual_specificity,
                            "suspicion_score": ev.evaluation.suspicion_score,
                        },
                        "weighted_turn_score": ev.weighted_turn_score,
                        "synthetic_likelihood_score": ev.synthetic_likelihood_score,
                        "probe_index": ev.probe_index,
                        "rationale": ev.rationale,
                    }
                    for ev in ledger.evaluations
                ],
                "socratic_probes": [
                    {
                        "turn_number": p.turn_number,
                        "competency_id": str(p.competency_id),
                        "probe_index": p.probe_index,
                        "strategy": p.strategy,
                        "target_concept": p.target_concept,
                        "probe_prompt": p.probe_prompt,
                        "prompt_hash": _ser_prompt_hash(p.prompt_hash),
                    }
                    for p in ledger.socratic_probes
                ],
                "prompt_hashes": [
                    {
                        "turn_number": ph.turn_number,
                        "role": ph.role,
                        "prompt_hash": _ser_prompt_hash(ph.prompt_hash),
                    }
                    for ph in ledger.prompt_hashes
                ],
                "telemetry_snapshots": [
                    {
                        "turn_number": ts.turn_number,
                        "payload": {
                            "focus_loss_count": ts.payload.focus_loss_count,
                            "focus_loss_duration_ms": ts.payload.focus_loss_duration_ms,
                            "external_paste_detected": ts.payload.external_paste_detected,
                            "paste_char_count": ts.payload.paste_char_count,
                            "keystroke_anomaly_detected": ts.payload.keystroke_anomaly_detected,
                            "response_latency_ms": ts.payload.response_latency_ms,
                            "speech_cadence_wpm": ts.payload.speech_cadence_wpm,
                            "pause_entropy": ts.payload.pause_entropy,
                        },
                        "p_client": ts.p_client,
                        "p_cadence": ts.p_cadence,
                        "p_stylo": ts.p_stylo,
                        "turn_suspicion_risk": ts.turn_suspicion_risk,
                    }
                    for ts in ledger.telemetry_snapshots
                ],
                "integrity_flags": [
                    {
                        "flag_code": fl.flag_code,
                        "severity": fl.severity,
                        "turn_number": fl.turn_number,
                        "confidence": float(fl.confidence),
                        "evidence_summary": fl.evidence_summary,
                    }
                    for fl in ledger.integrity_flags
                ],
            },
        }
        canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return canonical, digest

    @staticmethod
    def verify_and_deserialize(
        *,
        state_json: str,
        expected_sha256: str,
    ) -> InterviewGraphState:
        """Verify SHA-256 digest before reconstructing InterviewGraphState."""
        actual_sha256 = hashlib.sha256(state_json.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise ExternalAdapterFailureError(
                "Checkpoint JSON failed SHA-256 integrity verification.",
                code="CHECKPOINT_CORRUPTION_DETECTED",
                retryable=False,
                metadata={
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                },
            )
        try:
            data: dict[str, Any] = json.loads(state_json)
        except json.JSONDecodeError as exc:
            raise ExternalAdapterFailureError(
                f"Corrupted checkpoint JSON payload: {exc}",
                code="CHECKPOINT_CORRUPTION_DETECTED",
                retryable=False,
            ) from exc

        m = data["metadata"]
        metadata = SessionMetadataPlane(
            session_id=UUID(str(m["session_id"])),
            tenant_id=TenantId.from_str(str(m["tenant_id"])),
            requisition_id=UUID(str(m["requisition_id"])),
            candidate_profile_id=UUID(str(m["candidate_profile_id"])),
            rubric_version=VersionNumber(int(m["rubric_version"])),
            started_at=datetime.fromisoformat(str(m["started_at"])),
            max_wall_clock_seconds=int(m["max_wall_clock_seconds"]),
            max_turns=int(m["max_turns"]),
            token_budget_limit=int(m["token_budget_limit"]),
            max_probes_per_competency=int(m["max_probes_per_competency"]),
        )

        r = data["rubric"]
        rubric = RubricContextPlane(
            competency_queue=tuple(
                CompetencyBriefingTargetDTO(
                    competency_id=UUID(str(c["competency_id"])),
                    canonical_skill_id=str(c["canonical_skill_id"]),
                    weight=float(c["weight"]),
                    target_depth=int(c["target_depth"]),
                    is_gatekeeper=bool(c["is_gatekeeper"]),
                    ideal_anchor_descriptor=str(c["ideal_anchor_descriptor"]),
                    probe_priority=float(c["probe_priority"]),
                )
                for c in r["competency_queue"]
            ),
            candidate_project_highlights=tuple(
                str(x) for x in r["candidate_project_highlights"]
            ),
        )

        rt = data["routing"]
        routing = AdaptiveRoutingPlane(
            phase=InterviewGraphPhase(str(rt["phase"])),
            active_competency_id=UUID(str(rt["active_competency_id"])),
            current_difficulty=DifficultyLevel.from_int(int(rt["current_difficulty"])),
            questions_asked_in_active_competency=int(
                rt["questions_asked_in_active_competency"]
            ),
            probes_used_in_active_competency=int(rt["probes_used_in_active_competency"]),
            mastery_by_competency=MappingProxyType(
                {UUID(k): float(v) for k, v in rt["mastery_by_competency"].items()}
            ),
            rolling_authenticity_index=float(rt["rolling_authenticity_index"]),
            total_tokens_consumed=int(rt["total_tokens_consumed"]),
            completed_turns=int(rt["completed_turns"]),
            active_prompt_text=str(rt["active_prompt_text"]),
            active_prompt_hash=_deser_prompt_hash(rt["active_prompt_hash"]),
            last_routing_action=(
                RoutingAction(str(rt["last_routing_action"]))
                if rt["last_routing_action"] is not None
                else None
            ),
            last_routing_rule_id=(
                str(rt["last_routing_rule_id"])
                if rt["last_routing_rule_id"] is not None
                else None
            ),
            synthesis_summary=(
                str(rt["synthesis_summary"])
                if rt["synthesis_summary"] is not None
                else None
            ),
        )

        lg = data["ledger"]
        ledger = DialogueLedgerPlane(
            turns=tuple(_deser_turn_record(t) for t in lg["turns"]),
            recent_dialogue_window=tuple(
                _deser_turn_record(t) for t in lg["recent_dialogue_window"]
            ),
            evaluations=tuple(
                EvaluatedTurnRecord(
                    turn_number=int(ev["turn_number"]),
                    competency_id=UUID(str(ev["competency_id"])),
                    difficulty=DifficultyLevel.from_int(int(ev["difficulty"])),
                    evaluation=TurnEvaluationScore(
                        technical_accuracy=float(ev["evaluation"]["technical_accuracy"]),
                        depth_of_understanding=float(
                            ev["evaluation"]["depth_of_understanding"]
                        ),
                        tradeoff_awareness=float(ev["evaluation"]["tradeoff_awareness"]),
                        contextual_specificity=float(
                            ev["evaluation"]["contextual_specificity"]
                        ),
                        suspicion_score=float(ev["evaluation"]["suspicion_score"]),
                    ),
                    weighted_turn_score=float(ev["weighted_turn_score"]),
                    synthetic_likelihood_score=float(ev["synthetic_likelihood_score"]),
                    probe_index=int(ev["probe_index"]),
                    rationale=str(ev["rationale"]),
                )
                for ev in lg["evaluations"]
            ),
            socratic_probes=tuple(
                SocraticProbeState(
                    turn_number=int(p["turn_number"]),
                    competency_id=UUID(str(p["competency_id"])),
                    probe_index=int(p["probe_index"]),
                    strategy=str(p["strategy"]),
                    target_concept=str(p["target_concept"]),
                    probe_prompt=str(p["probe_prompt"]),
                    prompt_hash=_deser_prompt_hash(p["prompt_hash"]),
                )
                for p in lg["socratic_probes"]
            ),
            prompt_hashes=tuple(
                PromptHashAuditRecord(
                    turn_number=int(ph["turn_number"]),
                    role=str(ph["role"]),
                    prompt_hash=_deser_prompt_hash(ph["prompt_hash"]),
                )
                for ph in lg["prompt_hashes"]
            ),
            telemetry_snapshots=tuple(
                ClientTelemetryRecord(
                    turn_number=int(ts["turn_number"]),
                    payload=ClientTelemetryPayloadDTO(
                        focus_loss_count=int(ts["payload"]["focus_loss_count"]),
                        focus_loss_duration_ms=int(ts["payload"]["focus_loss_duration_ms"]),
                        external_paste_detected=bool(
                            ts["payload"]["external_paste_detected"]
                        ),
                        paste_char_count=int(ts["payload"]["paste_char_count"]),
                        keystroke_anomaly_detected=bool(
                            ts["payload"]["keystroke_anomaly_detected"]
                        ),
                        response_latency_ms=int(ts["payload"]["response_latency_ms"]),
                        speech_cadence_wpm=float(ts["payload"]["speech_cadence_wpm"]),
                        pause_entropy=float(ts["payload"]["pause_entropy"]),
                    ),
                    p_client=float(ts["p_client"]),
                    p_cadence=float(ts["p_cadence"]),
                    p_stylo=float(ts["p_stylo"]),
                    turn_suspicion_risk=float(ts["turn_suspicion_risk"]),
                )
                for ts in lg["telemetry_snapshots"]
            ),
            integrity_flags=tuple(
                IntegrityFlag(
                    flag_code=str(fl["flag_code"]),
                    severity=str(fl["severity"]),
                    turn_number=int(fl["turn_number"]),
                    confidence=ConfidenceScore(float(fl["confidence"])),
                    evidence_summary=str(fl["evidence_summary"]),
                )
                for fl in lg["integrity_flags"]
            ),
        )

        return InterviewGraphState(
            metadata=metadata,
            rubric=rubric,
            routing=routing,
            ledger=ledger,
            checkpoint_version=str(data["checkpoint_version"]),
            fencing_token=int(data["fencing_token"]),
        )


@dataclass(slots=True)
class _L1CacheEntry:
    record: CheckpointSnapshotRecord
    expires_at_monotonic: float


class TwoTierFencedStateCheckpointer:
    """ADR-009 Checkpointer combining TTL L1 cache with ACID SQL L2 & SHA-256 checks."""

    def __init__(
        self,
        *,
        db: SqlDatabaseManager,
        l1_ttl_seconds: float = 300.0,
        clock_fn: Callable[[], float] = time.monotonic,
        serializer: InterviewStateSerializer | None = None,
    ) -> None:
        self._db = db
        self._l1_ttl_seconds = max(0.0, float(l1_ttl_seconds))
        self._clock_fn = clock_fn
        self._serializer = serializer if serializer is not None else InterviewStateSerializer()
        self._l1_latest: dict[tuple[UUID, UUID], _L1CacheEntry] = {}
        self._l1_by_version: dict[tuple[UUID, UUID, str], _L1CacheEntry] = {}
        self._l1_hits: int = 0
        self._l2_hits: int = 0

    @property
    def l1_hit_count(self) -> int:
        return self._l1_hits

    @property
    def l2_hit_count(self) -> int:
        return self._l2_hits

    def invalidate_l1(
        self,
        tenant_id: TenantId | None = None,
        session_id: UUID | None = None,
    ) -> None:
        """Evict L1 hot cache entries (full flush or scoped to tenant/session)."""
        if tenant_id is None and session_id is None:
            self._l1_latest.clear()
            self._l1_by_version.clear()
            return
        target_tid = tenant_id.value if tenant_id is not None else None
        for key in list(self._l1_latest.keys()):
            if (target_tid is None or key[0] == target_tid) and (
                session_id is None or key[1] == session_id
            ):
                del self._l1_latest[key]
        for vkey in list(self._l1_by_version.keys()):
            if (target_tid is None or vkey[0] == target_tid) and (
                session_id is None or vkey[1] == session_id
            ):
                del self._l1_by_version[vkey]

    def _put_l1(self, record: CheckpointSnapshotRecord) -> None:
        if self._l1_ttl_seconds <= 0.0:
            return
        expires = self._clock_fn() + self._l1_ttl_seconds
        entry = _L1CacheEntry(record=record, expires_at_monotonic=expires)
        self._l1_latest[(record.tenant_id.value, record.session_id)] = entry
        self._l1_by_version[
            (record.tenant_id.value, record.session_id, record.checkpoint_version)
        ] = entry

    def _get_l1(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        checkpoint_version: str | None = None,
    ) -> CheckpointSnapshotRecord | None:
        now = self._clock_fn()
        if checkpoint_version is None:
            key = (tenant_id.value, session_id)
            entry = self._l1_latest.get(key)
            if entry is None:
                return None
            if now >= entry.expires_at_monotonic:
                del self._l1_latest[key]
                return None
            return entry.record
        vkey = (tenant_id.value, session_id, checkpoint_version)
        entry = self._l1_by_version.get(vkey)
        if entry is None:
            return None
        if now >= entry.expires_at_monotonic:
            del self._l1_by_version[vkey]
            return None
        return entry.record

    def _row_to_snapshot(self, row: sqlite3.Row) -> CheckpointSnapshotRecord:
        state = self._serializer.verify_and_deserialize(
            state_json=str(row["state_json"]),
            expected_sha256=str(row["state_sha256"]),
        )
        return CheckpointSnapshotRecord(
            tenant_id=TenantId.from_str(str(row["tenant_id"])),
            session_id=UUID(str(row["session_id"])),
            checkpoint_version=str(row["checkpoint_version"]),
            step_number=int(row["step_number"]),
            fencing_token=int(row["fencing_token"]),
            saved_at=datetime.fromisoformat(str(row["saved_at"])),
            state=state,
        )

    def save_checkpoint(
        self,
        state: InterviewGraphState,
        *,
        expected_previous_version: str | None = None,
    ) -> CheckpointSnapshotRecord:
        """Persist fenced checkpoint atomically into L2 SQL and warm L1 hot cache."""
        tenant_id = state.metadata.tenant_id
        session_id = state.metadata.session_id
        t_str = str(tenant_id.value)
        s_str = str(session_id)

        with self._db.write_transaction():
            cross_row = self._db.fetchone(
                "SELECT tenant_id FROM interview_state_checkpoints WHERE session_id = ? LIMIT 1",
                (s_str,),
            )
            if cross_row is not None and str(cross_row["tenant_id"]) != t_str:
                raise UnauthorizedTenantAccessError(
                    "Checkpoint tenant boundary violation detected.",
                    code="CHECKPOINT_TENANT_MISMATCH",
                )

            latest_row = self._db.fetchone(
                "SELECT checkpoint_version, step_number, fencing_token "
                "FROM interview_state_checkpoints "
                "WHERE tenant_id = ? AND session_id = ? "
                "ORDER BY step_number DESC LIMIT 1",
                (t_str, s_str),
            )
            if latest_row is not None:
                latest_fence = int(latest_row["fencing_token"])
                latest_ver = str(latest_row["checkpoint_version"])
                next_step = int(latest_row["step_number"]) + 1
                parent_ver: str | None = latest_ver
                if state.fencing_token <= latest_fence:
                    raise ResourceConflictError(
                        (
                            f"Stale fencing token {state.fencing_token} rejected; "
                            f"latest committed fence is {latest_fence}."
                        ),
                        code="STALE_FENCING_TOKEN",
                        metadata={
                            "incoming_fence": state.fencing_token,
                            "latest_fence": latest_fence,
                            "session_id": s_str,
                        },
                    )
                if (
                    expected_previous_version is not None
                    and expected_previous_version != latest_ver
                ):
                    raise ResourceConflictError(
                        (
                            f"Checkpoint version conflict: expected {latest_ver!r}, "
                            f"got {expected_previous_version!r}."
                        ),
                        code="CHECKPOINT_VERSION_CONFLICT",
                        metadata={
                            "expected_version": latest_ver,
                            "provided_version": expected_previous_version,
                        },
                    )
            else:
                next_step = 1
                parent_ver = None

            state_json, state_sha256 = self._serializer.serialize(state)
            saved_at = utc_now()
            self._db.execute(
                "INSERT INTO interview_state_checkpoints ("
                "tenant_id, session_id, checkpoint_version, step_number, fencing_token, "
                "parent_checkpoint_version, state_json, state_sha256, saved_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    t_str,
                    s_str,
                    state.checkpoint_version,
                    next_step,
                    state.fencing_token,
                    parent_ver,
                    state_json,
                    state_sha256,
                    saved_at.isoformat(),
                ),
            )

        record = CheckpointSnapshotRecord(
            tenant_id=tenant_id,
            session_id=session_id,
            checkpoint_version=state.checkpoint_version,
            step_number=next_step,
            fencing_token=state.fencing_token,
            saved_at=saved_at,
            state=state,
        )
        self._put_l1(record)
        return record

    def put_checkpoint(
        self,
        state: InterviewGraphState,
        *,
        expected_previous_version: str | None = None,
        fencing_token: int | None = None,
    ) -> CheckpointSnapshotRecord:
        """Explicit two-tier put API supporting optional overriding fencing_token."""
        effective_state = (
            replace(state, fencing_token=fencing_token)
            if fencing_token is not None
            else state
        )
        return self.save_checkpoint(
            effective_state,
            expected_previous_version=expected_previous_version,
        )

    def get_checkpoint(
        self,
        tenant_id: TenantId,
        session_id: UUID,
        *,
        checkpoint_version: str | None = None,
    ) -> CheckpointSnapshotRecord | None:
        """Read latest or specific checkpoint with L1 hit -> L2 SQL fallback & repopulation."""
        if checkpoint_version is None:
            return self.load_latest(tenant_id, session_id)
        return self.load_version(tenant_id, session_id, checkpoint_version)

    def load_latest(
        self,
        tenant_id: TenantId,
        session_id: UUID,
    ) -> CheckpointSnapshotRecord | None:
        """Load latest tenant-scoped checkpoint from L1 hot cache or L2 SQL store."""
        l1_hit = self._get_l1(tenant_id=tenant_id, session_id=session_id)
        if l1_hit is not None:
            self._l1_hits += 1
            return l1_hit

        row = self._db.fetchone(
            "SELECT * FROM interview_state_checkpoints "
            "WHERE tenant_id = ? AND session_id = ? ORDER BY step_number DESC LIMIT 1",
            (str(tenant_id.value), str(session_id)),
        )
        if row is None:
            return None
        record = self._row_to_snapshot(row)
        self._l2_hits += 1
        self._put_l1(record)
        return record

    def load_version(
        self,
        tenant_id: TenantId,
        session_id: UUID,
        checkpoint_version: str,
    ) -> CheckpointSnapshotRecord | None:
        """Load specific checkpoint version from L1 cache or L2 durable SQL table."""
        l1_hit = self._get_l1(
            tenant_id=tenant_id,
            session_id=session_id,
            checkpoint_version=checkpoint_version,
        )
        if l1_hit is not None:
            self._l1_hits += 1
            return l1_hit

        row = self._db.fetchone(
            "SELECT * FROM interview_state_checkpoints "
            "WHERE tenant_id = ? AND session_id = ? AND checkpoint_version = ?",
            (str(tenant_id.value), str(session_id), checkpoint_version),
        )
        if row is None:
            return None
        record = self._row_to_snapshot(row)
        self._l2_hits += 1
        self._put_l1(record)
        return record

    def list_history(
        self,
        tenant_id: TenantId,
        session_id: UUID,
    ) -> tuple[CheckpointSnapshotRecord, ...]:
        """Return ordered historical checkpoints from authoritative L2 durable store."""
        rows = self._db.fetchall(
            "SELECT * FROM interview_state_checkpoints "
            "WHERE tenant_id = ? AND session_id = ? ORDER BY step_number ASC",
            (str(tenant_id.value), str(session_id)),
        )
        return tuple(self._row_to_snapshot(row) for row in rows)
