"""Transactional Outbox Relay Worker & Vector/PII Projection Synchronizer."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from talent_screener.application.common.dtos import (
    DeadLetterRecordDTO,
    FailureClassification,
)
from talent_screener.application.common.ports.outbound import (
    DeadLetterQueuePort,
    DomainEventBusPort,
    PiiVaultPort,
)
from talent_screener.application.document_intelligence.dtos import (
    ExtractedCandidateDossierDTO,
    ExtractedProjectDTO,
    ExtractedWorkExperienceDTO,
)
from talent_screener.application.document_intelligence.ports.outbound import (
    HybridVectorSearchPort,
)
from talent_screener.domain.contexts.document_intelligence import CandidateProfile
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.persistence.relational.repositories import (
    SqlCandidateProfileRepository,
)
from talent_screener.infrastructure.persistence.relational.schema import SqlDatabaseManager
from talent_screener.infrastructure.persistence.relational.unit_of_work import (
    OutboxEventCodec,
)
from talent_screener.infrastructure.persistence.vector_store.quantized_backend import (
    QuantizedHybridVectorStoreBackend,
)


class OutboxStatus(StrEnum):
    """Lifecycle states of a Transactional Outbox row."""

    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    DEAD_LETTERED = "DEAD_LETTERED"


@dataclass(frozen=True, slots=True)
class OutboxEventRecord:
    """Immutable snapshot of a row in the transactional_outbox table."""

    outbox_id: UUID
    tenant_id: TenantId
    aggregate_type: str
    aggregate_id: UUID
    event_id: UUID
    event_type: str
    schema_version: str
    payload_json: str
    occurred_at: datetime
    status: OutboxStatus
    retry_count: int
    last_error: str | None
    next_retry_at: datetime | None
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class OutboxDispatchReceipt:
    """Summary returned after one polling batch of the Transactional Outbox Relay."""

    polled_count: int
    published_count: int
    retry_scheduled_count: int
    dead_lettered_count: int
    published_event_ids: tuple[UUID, ...]


def profile_to_dossier(profile: CandidateProfile) -> ExtractedCandidateDossierDTO:
    """Convert a persisted zero-PII CandidateProfile aggregate into an indexing dossier DTO."""
    sections: list[str] = []
    for exp in profile.work_experiences:
        sections.append(f"{exp.role_title} ({exp.normalized_role}) at {exp.company_token}")
    for proj in profile.projects:
        sections.append(f"{proj.project_name}: {proj.summary}. {proj.source_quote}")
    for claim in profile.skill_claims:
        sections.append(f"{claim.raw_label}: {claim.source_quote}")

    seniority = profile.seniority_estimate
    return ExtractedCandidateDossierDTO(
        anonymized_candidate_token=profile.anonymized_token,
        schema_version="1.0.0",
        raw_redacted_text="\n".join(sections) if sections else "Verified candidate profile",
        pii_redaction_map=profile.pii_redaction_map,
        work_experiences=tuple(
            ExtractedWorkExperienceDTO(
                role_title=exp.role_title,
                normalized_role=exp.normalized_role,
                company_token=exp.company_token,
                start_year=exp.start_year,
                start_month=exp.start_month,
                end_year=exp.end_year,
                end_month=exp.end_month,
                is_current=exp.is_current,
                employment_type=exp.employment_type,
            )
            for exp in profile.work_experiences
        ),
        projects=tuple(
            ExtractedProjectDTO(
                project_name=proj.project_name,
                scale_tier=proj.scale_tier,
                summary=proj.summary,
                source_quote=proj.source_quote,
                impact_metrics=proj.impact_metrics,
            )
            for proj in profile.projects
        ),
        skill_claims=profile.skill_claims,
        seniority_estimate=seniority,
        extraction_confidence=seniority.confidence,
    )


def lookup_requisition_for_profile(
    db: SqlDatabaseManager,
    *,
    tenant_id: TenantId,
    candidate_profile_id: UUID,
) -> UUID | None:
    """Resolve originating JobRequisition ID for a candidate profile if ingested via batch."""
    row = db.fetchone(
        "SELECT b.requisition_id "
        "FROM document_ingestion_tasks t "
        "JOIN screening_batches b ON b.batch_id = t.batch_id "
        "WHERE t.tenant_id = ? AND t.candidate_profile_id = ? "
        "ORDER BY t.task_id DESC LIMIT 1",
        (str(tenant_id.value), str(candidate_profile_id)),
    )
    return UUID(str(row["requisition_id"])) if row is not None else None


class TransactionalOutboxRelayWorker:
    """Polling worker relaying Transactional Outbox events to Vector/Vault projections & DLQ."""

    def __init__(
        self,
        *,
        db: SqlDatabaseManager,
        profile_repo: SqlCandidateProfileRepository,
        vector_search: HybridVectorSearchPort | None = None,
        vector_backend: QuantizedHybridVectorStoreBackend | None = None,
        pii_vault: PiiVaultPort | None = None,
        event_bus: DomainEventBusPort | None = None,
        dlq: DeadLetterQueuePort | None = None,
        codec: OutboxEventCodec | None = None,
        max_retries: int = 3,
        base_retry_delay_seconds: float = 1.0,
        relay_to_event_bus: bool = False,
        clock_fn: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._profile_repo = profile_repo
        self._vector_search = vector_search
        self._vector_backend = vector_backend
        self._pii_vault = pii_vault
        self._event_bus = event_bus
        self._dlq = dlq
        self._codec = codec if codec is not None else OutboxEventCodec()
        self._max_retries = max(1, max_retries)
        self._base_retry_delay = max(0.0, base_retry_delay_seconds)
        self._relay_to_event_bus = relay_to_event_bus
        self._clock_fn = clock_fn

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> OutboxEventRecord:
        return OutboxEventRecord(
            outbox_id=UUID(str(row["outbox_id"])),
            tenant_id=TenantId.from_str(str(row["tenant_id"])),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=UUID(str(row["aggregate_id"])),
            event_id=UUID(str(row["event_id"])),
            event_type=str(row["event_type"]),
            schema_version=str(row["schema_version"]),
            payload_json=str(row["payload_json"]),
            occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
            status=OutboxStatus(str(row["status"])),
            retry_count=int(row["retry_count"]),
            last_error=str(row["last_error"]) if row["last_error"] is not None else None,
            next_retry_at=(
                datetime.fromisoformat(str(row["next_retry_at"]))
                if row["next_retry_at"] is not None
                else None
            ),
            published_at=(
                datetime.fromisoformat(str(row["published_at"]))
                if row["published_at"] is not None
                else None
            ),
        )

    def list_records(
        self,
        *,
        tenant_id: TenantId | None = None,
        status: OutboxStatus | None = None,
    ) -> tuple[OutboxEventRecord, ...]:
        """Inspect persisted transactional_outbox rows ordered by occurrence timestamp."""
        clauses: list[str] = []
        params: list[object] = []
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(str(tenant_id.value))
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.fetchall(
            f"SELECT * FROM transactional_outbox {where_sql} ORDER BY occurred_at ASC",
            params,
        )
        return tuple(self._row_to_record(r) for r in rows)

    async def _project_event(self, record: OutboxEventRecord) -> None:
        payload: dict[str, object] = json.loads(record.payload_json)
        if (
            record.event_type == "CandidateProfileNormalizedEvent"
            and self._vector_search is not None
        ):
            profile_id = UUID(str(payload["candidate_profile_id"]))
            profile = self._profile_repo.get_by_id(record.tenant_id, profile_id)
            if profile is not None:
                target_ver = int(profile.version)
                current_ver = (
                    self._vector_backend.get_sync_version(
                        tenant_id=record.tenant_id,
                        candidate_profile_id=profile.id,
                    )
                    if self._vector_backend is not None
                    else None
                )
                if current_ver is None or current_ver < target_ver:
                    req_id = (
                        lookup_requisition_for_profile(
                            self._db,
                            tenant_id=record.tenant_id,
                            candidate_profile_id=profile.id,
                        )
                        or profile.id
                    )
                    await self._vector_search.index_candidate_dossier(
                        tenant_id=record.tenant_id,
                        requisition_id=req_id,
                        candidate_profile_id=profile.id,
                        dossier=profile_to_dossier(profile),
                        sync_version=target_ver,
                    )

        elif record.event_type == "CandidateErasureRequestedEvent":
            candidate_id = UUID(str(payload["candidate_id"]))
            reason = str(payload.get("reason", "Outbox GDPR Erasure"))
            if self._pii_vault is not None:
                await self._pii_vault.crypto_shred(
                    tenant_id=record.tenant_id,
                    candidate_id=candidate_id,
                    reason=reason,
                )
            if self._vector_search is not None:
                await self._vector_search.delete_candidate_vectors(
                    tenant_id=record.tenant_id,
                    candidate_profile_id=candidate_id,
                )
            elif self._vector_backend is not None:
                await self._vector_backend.delete_by_candidate(
                    tenant_id=record.tenant_id,
                    candidate_profile_id=candidate_id,
                )

        if self._relay_to_event_bus and self._event_bus is not None:
            event_obj = self._codec.deserialize(record.event_type, record.payload_json)
            if event_obj is not None:
                await self._event_bus.publish(event_obj)

    async def poll_and_dispatch(
        self,
        *,
        tenant_id: TenantId | None = None,
        limit: int = 100,
    ) -> OutboxDispatchReceipt:
        """Poll due PENDING/FAILED_RETRYABLE rows and dispatch with retry & DLQ safety."""
        now = self._clock_fn()
        now_iso = now.isoformat()
        clauses = [
            "status IN (?, ?)",
            "(next_retry_at IS NULL OR next_retry_at <= ?)",
        ]
        params: list[object] = [
            OutboxStatus.PENDING.value,
            OutboxStatus.FAILED_RETRYABLE.value,
            now_iso,
        ]
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(str(tenant_id.value))
        params.append(max(1, limit))

        rows = self._db.fetchall(
            f"SELECT * FROM transactional_outbox WHERE {' AND '.join(clauses)} "
            "ORDER BY occurred_at ASC LIMIT ?",
            params,
        )
        records = [self._row_to_record(row) for row in rows]
        published_ids: list[UUID] = []
        retry_count = 0
        dlq_count = 0

        for rec in records:
            try:
                await self._project_event(rec)
            except Exception as exc:
                next_attempt = rec.retry_count + 1
                err_msg = str(exc)[:500] or type(exc).__name__
                if next_attempt >= self._max_retries:
                    dlq_count += 1
                    with self._db.write_transaction():
                        self._db.execute(
                            "UPDATE transactional_outbox SET status = ?, retry_count = ?, "
                            "last_error = ? WHERE outbox_id = ? AND tenant_id = ?",
                            (
                                OutboxStatus.DEAD_LETTERED.value,
                                next_attempt,
                                err_msg,
                                str(rec.outbox_id),
                                str(rec.tenant_id.value),
                            ),
                        )
                    if self._dlq is not None:
                        await self._dlq.enqueue(
                            DeadLetterRecordDTO(
                                tenant_id=rec.tenant_id,
                                task_name=f"outbox.{rec.event_type}",
                                correlation_id=str(rec.outbox_id),
                                payload={
                                    "outbox_id": str(rec.outbox_id),
                                    "event_id": str(rec.event_id),
                                    "aggregate_id": str(rec.aggregate_id),
                                    "event_type": rec.event_type,
                                },
                                error_code="OUTBOX_RELAY_EXHAUSTED",
                                error_message=err_msg,
                                attempt_count=next_attempt,
                                classification=FailureClassification.TRANSIENT,
                            )
                        )
                else:
                    retry_count += 1
                    delay = self._base_retry_delay * (2 ** (next_attempt - 1))
                    next_retry_ts = (now + timedelta(seconds=delay)).isoformat()
                    with self._db.write_transaction():
                        self._db.execute(
                            "UPDATE transactional_outbox SET status = ?, retry_count = ?, "
                            "last_error = ?, next_retry_at = ? "
                            "WHERE outbox_id = ? AND tenant_id = ?",
                            (
                                OutboxStatus.FAILED_RETRYABLE.value,
                                next_attempt,
                                err_msg,
                                next_retry_ts,
                                str(rec.outbox_id),
                                str(rec.tenant_id.value),
                            ),
                        )
            else:
                published_ids.append(rec.event_id)
                with self._db.write_transaction():
                    self._db.execute(
                        "UPDATE transactional_outbox SET status = ?, published_at = ?, "
                        "last_error = NULL WHERE outbox_id = ? AND tenant_id = ?",
                        (
                            OutboxStatus.PUBLISHED.value,
                            self._clock_fn().isoformat(),
                            str(rec.outbox_id),
                            str(rec.tenant_id.value),
                        ),
                    )

        return OutboxDispatchReceipt(
            polled_count=len(records),
            published_count=len(published_ids),
            retry_scheduled_count=retry_count,
            dead_lettered_count=dlq_count,
            published_event_ids=tuple(published_ids),
        )
