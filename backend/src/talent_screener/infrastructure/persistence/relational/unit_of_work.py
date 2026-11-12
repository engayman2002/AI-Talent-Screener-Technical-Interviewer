"""ACID SQL Transactional Unit of Work with Transactional Outbox Persistence."""

from __future__ import annotations

import dataclasses
import json
import typing
from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from talent_screener.application.common.ports.outbound import DomainEventBusPort
from talent_screener.domain.contexts.document_intelligence.events import (
    CandidateProfileNormalizedEvent,
    DocumentExtractionFailedEvent,
    DocumentExtractionSucceededEvent,
    ScreeningBatchCompletedEvent,
    ScreeningBatchCreatedEvent,
)
from talent_screener.domain.contexts.document_intelligence.matching.events import (
    CandidateMatchedToJobEvent,
    CandidateRejectedByGatekeeperEvent,
    CandidateShortlistedForInterviewEvent,
)
from talent_screener.domain.contexts.evaluation_analytics.events import (
    EvaluationReportGeneratedEvent,
    HighRiskIntegrityAlertRaisedEvent,
    RecruiterDecisionRecordedEvent,
)
from talent_screener.domain.contexts.identity_governance.events import (
    CandidateErasureRequestedEvent,
)
from talent_screener.domain.contexts.live_interview.events import (
    AdaptiveTurnCompletedEvent,
    IntegrityAnomalyDetectedEvent,
    InterviewSessionCompletedEvent,
    InterviewSessionStartedEvent,
    InterviewSessionTerminatedEarlyEvent,
    SocraticProbeTriggeredEvent,
)
from talent_screener.domain.contexts.recruitment.events import (
    JobRequisitionArchivedEvent,
    JobRequisitionPublishedEvent,
    JobRequisitionRubricUpdatedEvent,
)
from talent_screener.domain.shared.base import AggregateRoot, DomainEvent
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.persistence.relational.schema import SqlDatabaseManager

_DOMAIN_EVENT_TYPES: tuple[type[DomainEvent], ...] = (
    JobRequisitionPublishedEvent,
    JobRequisitionRubricUpdatedEvent,
    JobRequisitionArchivedEvent,
    ScreeningBatchCreatedEvent,
    DocumentExtractionSucceededEvent,
    DocumentExtractionFailedEvent,
    CandidateProfileNormalizedEvent,
    ScreeningBatchCompletedEvent,
    CandidateMatchedToJobEvent,
    CandidateShortlistedForInterviewEvent,
    CandidateRejectedByGatekeeperEvent,
    InterviewSessionStartedEvent,
    AdaptiveTurnCompletedEvent,
    SocraticProbeTriggeredEvent,
    IntegrityAnomalyDetectedEvent,
    InterviewSessionCompletedEvent,
    InterviewSessionTerminatedEarlyEvent,
    EvaluationReportGeneratedEvent,
    HighRiskIntegrityAlertRaisedEvent,
    RecruiterDecisionRecordedEvent,
    CandidateErasureRequestedEvent,
)


def _encode_value(value: object) -> object:
    if isinstance(value, TenantId):
        return str(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_encode_value(item) for item in value]
    return value


def _decode_value(raw: Any, target_type: Any) -> Any:
    if target_type is TenantId:
        return TenantId.from_str(str(raw))
    if target_type is UUID:
        return UUID(str(raw))
    if target_type is datetime:
        return datetime.fromisoformat(str(raw))
    origin = typing.get_origin(target_type)
    if origin is tuple:
        args = typing.get_args(target_type)
        elem_type = args[0] if args else str
        assert isinstance(raw, list)
        return tuple(_decode_value(item, elem_type) for item in raw)
    if target_type is int:
        return int(raw)
    if target_type is float:
        return float(raw)
    if target_type is bool:
        return bool(raw)
    return str(raw)


class OutboxEventCodec:
    """Deterministic JSON encoder/decoder for all DomainEvent types stored in Outbox."""

    def __init__(self) -> None:
        self._registry: dict[str, type[DomainEvent]] = {
            cls.__name__: cls for cls in _DOMAIN_EVENT_TYPES
        }

    def register(self, event_cls: type[DomainEvent]) -> None:
        """Register an additional custom DomainEvent class for outbox replay."""
        self._registry[event_cls.__name__] = event_cls

    def serialize(self, event: DomainEvent) -> str:
        """Serialize a DomainEvent dataclass into deterministic canonical JSON."""
        data = {
            field.name: _encode_value(getattr(event, field.name))
            for field in dataclasses.fields(event)
        }
        return json.dumps(data, separators=(",", ":"), sort_keys=True)

    def deserialize(self, event_type: str, payload_json: str) -> DomainEvent | None:
        """Reconstruct a typed DomainEvent instance from stored outbox JSON."""
        cls = self._registry.get(event_type)
        if cls is None:
            return None
        raw_dict: dict[str, Any] = json.loads(payload_json)
        hints = typing.get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for field in dataclasses.fields(cls):
            if field.name in raw_dict:
                kwargs[field.name] = _decode_value(
                    raw_dict[field.name], hints.get(field.name, str)
                )
        return cls(**kwargs)


class SqlTransactionalUnitOfWork:
    """SQLite/PostgreSQL ACID TransactionalUnitOfWorkPort with Transactional Outbox."""

    def __init__(
        self,
        *,
        db: SqlDatabaseManager,
        event_bus: DomainEventBusPort | None = None,
        dispatch_to_event_bus_on_commit: bool = True,
        codec: OutboxEventCodec | None = None,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._dispatch_to_bus = dispatch_to_event_bus_on_commit
        self._codec = codec if codec is not None else OutboxEventCodec()
        self._active_tenant_id: TenantId | None = None
        self._aggregates: list[AggregateRoot[Any]] = []
        self._seen_aggregate_ids: set[int] = set()
        self._dispatched_events: list[DomainEvent] = []
        self.commit_count: int = 0
        self.rollback_count: int = 0

    @property
    def dispatched_events(self) -> tuple[DomainEvent, ...]:
        """Return immutable tuple of post-commit dispatched Domain Events."""
        return tuple(self._dispatched_events)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None or self._db.in_transaction:
            await self.rollback()

    async def begin(self, tenant_id: TenantId) -> None:
        """Start an explicit tenant-scoped ACID database transaction."""
        self._active_tenant_id = tenant_id
        self._aggregates.clear()
        self._seen_aggregate_ids.clear()
        self._db.begin_transaction()

    def register_aggregate(self, aggregate: AggregateRoot[Any]) -> None:
        """Track an aggregate root whose queued Domain Events must enter Outbox on commit."""
        obj_id = id(aggregate)
        if obj_id not in self._seen_aggregate_ids:
            self._seen_aggregate_ids.add(obj_id)
            self._aggregates.append(aggregate)

    def collect_pending_events(self) -> tuple[DomainEvent, ...]:
        """Drain all queued Domain Events from tracked aggregates."""
        drained: list[DomainEvent] = []
        for agg in self._aggregates:
            drained.extend(agg.pull_domain_events())
        return tuple(drained)

    def _insert_outbox_events(
        self,
        events: tuple[DomainEvent, ...],
        aggregate_types_by_id: dict[UUID, str],
    ) -> None:
        if not events:
            return
        rows = [
            (
                str(generate_uuid7()),
                str(event.tenant_id.value),
                aggregate_types_by_id.get(event.aggregate_id, "AggregateRoot"),
                str(event.aggregate_id),
                str(event.event_id),
                event.event_name,
                event.schema_version,
                self._codec.serialize(event),
                event.occurred_at.isoformat(),
                "PENDING",
                0,
                None,
                None,
                None,
            )
            for event in events
        ]
        self._db.executemany(
            "INSERT INTO transactional_outbox ("
            "outbox_id, tenant_id, aggregate_type, aggregate_id, event_id, "
            "event_type, schema_version, payload_json, occurred_at, status, "
            "retry_count, last_error, next_retry_at, published_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    async def commit(self) -> None:
        """Atomically persist Outbox rows in DB transaction, COMMIT, then dispatch events."""
        aggregate_types_by_id: dict[UUID, str] = {}
        for agg in self._aggregates:
            if isinstance(agg.id, UUID):
                aggregate_types_by_id[agg.id] = type(agg).__name__
            else:
                aggregate_types_by_id[agg.tenant_id.value] = type(agg).__name__

        events = self.collect_pending_events()
        self._aggregates.clear()
        self._seen_aggregate_ids.clear()

        try:
            self._insert_outbox_events(events, aggregate_types_by_id)
            self._db.commit_transaction()
        except Exception:
            self._db.rollback_transaction()
            self.rollback_count += 1
            raise

        self.commit_count += 1
        self._active_tenant_id = None
        if events:
            self._dispatched_events.extend(events)
            if self._dispatch_to_bus and self._event_bus is not None:
                await self._event_bus.publish_all(events)

    async def rollback(self) -> None:
        """Discard queued Domain Events and issue SQL ROLLBACK cleanly."""
        for agg in self._aggregates:
            agg.pull_domain_events()
        self._aggregates.clear()
        self._seen_aggregate_ids.clear()
        self._active_tenant_id = None
        self._db.rollback_transaction()
        self.rollback_count += 1
