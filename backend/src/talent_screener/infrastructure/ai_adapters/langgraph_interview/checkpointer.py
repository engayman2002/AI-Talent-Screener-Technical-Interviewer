"""ADR-009 Tenant-Isolated Fenced State Checkpointer for LangGraph Execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.common.exceptions import (
    ResourceConflictError,
    UnauthorizedTenantAccessError,
)
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.ai_adapters.langgraph_interview.state import (
    InterviewGraphState,
)


@dataclass(frozen=True, slots=True)
class CheckpointSnapshotRecord:
    """Immutable versioned checkpoint envelope persisted after cognitive transitions."""

    tenant_id: TenantId
    session_id: UUID
    checkpoint_version: str
    step_number: int
    fencing_token: int
    saved_at: datetime
    state: InterviewGraphState


@runtime_checkable
class InterviewStateCheckpointerProtocol(Protocol):
    """Port for persisting and restoring fenced LangGraph interview checkpoints."""

    def save_checkpoint(
        self,
        state: InterviewGraphState,
        *,
        expected_previous_version: str | None = None,
    ) -> CheckpointSnapshotRecord: ...

    def load_latest(
        self, tenant_id: TenantId, session_id: UUID
    ) -> CheckpointSnapshotRecord | None: ...

    def load_version(
        self,
        tenant_id: TenantId,
        session_id: UUID,
        checkpoint_version: str,
    ) -> CheckpointSnapshotRecord | None: ...

    def list_history(
        self, tenant_id: TenantId, session_id: UUID
    ) -> tuple[CheckpointSnapshotRecord, ...]: ...


class InMemoryFencedStateCheckpointer:
    """In-memory ADR-009 checkpointer enforcing tenant isolation and monotonic fencing tokens."""

    def __init__(self) -> None:
        self._history_by_session: dict[tuple[UUID, UUID], list[CheckpointSnapshotRecord]] = {}
        self._versions_by_key: dict[tuple[UUID, UUID, str], CheckpointSnapshotRecord] = {}

    def save_checkpoint(
        self,
        state: InterviewGraphState,
        *,
        expected_previous_version: str | None = None,
    ) -> CheckpointSnapshotRecord:
        """Persist snapshot only if fencing token is monotonic and tenant/version match."""
        tenant_id = state.metadata.tenant_id
        session_id = state.metadata.session_id
        session_key = (tenant_id.value, session_id)
        existing_history = self._history_by_session.get(session_key, [])

        if existing_history:
            latest = existing_history[-1]
            if latest.tenant_id != tenant_id:
                raise UnauthorizedTenantAccessError(
                    "Checkpoint tenant boundary violation detected.",
                    code="CHECKPOINT_TENANT_MISMATCH",
                )
            if state.fencing_token <= latest.fencing_token:
                raise ResourceConflictError(
                    (
                        f"Stale fencing token {state.fencing_token} rejected; "
                        f"latest committed fence is {latest.fencing_token}."
                    ),
                    code="STALE_FENCING_TOKEN",
                    metadata={
                        "incoming_fence": state.fencing_token,
                        "latest_fence": latest.fencing_token,
                        "session_id": str(session_id),
                    },
                )
            if (
                expected_previous_version is not None
                and expected_previous_version != latest.checkpoint_version
            ):
                raise ResourceConflictError(
                    (
                        f"Checkpoint version conflict: expected {latest.checkpoint_version!r}, "
                        f"got {expected_previous_version!r}."
                    ),
                    code="CHECKPOINT_VERSION_CONFLICT",
                    metadata={
                        "expected_version": latest.checkpoint_version,
                        "provided_version": expected_previous_version,
                    },
                )

        record = CheckpointSnapshotRecord(
            tenant_id=tenant_id,
            session_id=session_id,
            checkpoint_version=state.checkpoint_version,
            step_number=len(existing_history) + 1,
            fencing_token=state.fencing_token,
            saved_at=utc_now(),
            state=state,
        )
        history = self._history_by_session.setdefault(session_key, [])
        history.append(record)
        self._versions_by_key[(tenant_id.value, session_id, record.checkpoint_version)] = record
        return record

    def load_latest(
        self, tenant_id: TenantId, session_id: UUID
    ) -> CheckpointSnapshotRecord | None:
        """Return latest committed checkpoint for the tenant-scoped session, or None."""
        history = self._history_by_session.get((tenant_id.value, session_id))
        if not history:
            return None
        return history[-1]

    def load_version(
        self,
        tenant_id: TenantId,
        session_id: UUID,
        checkpoint_version: str,
    ) -> CheckpointSnapshotRecord | None:
        """Restore a specific checkpoint version within the owning tenant scope."""
        return self._versions_by_key.get((tenant_id.value, session_id, checkpoint_version))

    def list_history(
        self, tenant_id: TenantId, session_id: UUID
    ) -> tuple[CheckpointSnapshotRecord, ...]:
        """Return chronological immutable tuple of checkpoints for audit/replay verification."""
        return tuple(self._history_by_session.get((tenant_id.value, session_id), ()))
