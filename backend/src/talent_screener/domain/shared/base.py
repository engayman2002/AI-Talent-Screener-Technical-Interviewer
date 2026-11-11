"""Tactical DDD base primitives: DomainEvent, Entity, and AggregateRoot."""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import (
    require_non_empty_str,
    require_non_nil_uuid,
    require_utc_datetime,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import TenantId, ValueObject, VersionNumber


@dataclass(frozen=True, slots=True)
class DomainEvent(ValueObject):
    """Immutable base Domain Event raised by Aggregate Roots on state transitions."""

    aggregate_id: UUID
    tenant_id: TenantId
    event_id: UUID = field(default_factory=generate_uuid7, kw_only=True)
    occurred_at: datetime = field(default_factory=utc_now, kw_only=True)
    schema_version: str = field(default="1.0.0", kw_only=True)

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.aggregate_id, "aggregate_id", code="INVALID_EVENT_AGGREGATE_ID")
        require_non_nil_uuid(self.event_id, "event_id", code="INVALID_EVENT_ID")
        if not isinstance(self.tenant_id, TenantId):
            raise InvariantViolationError(
                "tenant_id must be a TenantId value object.",
                code="INVALID_TENANT_ID",
            )
        normalized_ts = require_utc_datetime(
            self.occurred_at,
            "occurred_at",
            code="INVALID_EVENT_TIMESTAMP",
        )
        object.__setattr__(self, "occurred_at", normalized_ts)
        cleaned_ver = require_non_empty_str(
            self.schema_version,
            "schema_version",
            max_len=32,
            code="INVALID_EVENT_SCHEMA_VERSION",
        )
        object.__setattr__(self, "schema_version", cleaned_ver)

    @property
    def event_name(self) -> str:
        """Canonical event type identifier."""
        return type(self).__name__


class Entity[TId: Hashable]:
    """Base tactical DDD Entity enforcing identity-based equality."""

    __slots__ = ("_id",)

    def __init__(self, entity_id: TId) -> None:
        self._id: TId = entity_id

    @property
    def id(self) -> TId:
        """Immutable identity of this entity."""
        return self._id

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        assert isinstance(other, Entity)
        return bool(self._id == other._id)

    def __hash__(self) -> int:
        return hash((type(self), self._id))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self._id!r})"


class AggregateRoot[TId: Hashable](Entity[TId]):
    """Base Aggregate Root managing optimistic concurrency version and domain event queue."""

    __slots__ = ("_domain_events", "_tenant_id", "_version")

    def __init__(
        self,
        entity_id: TId,
        *,
        tenant_id: TenantId,
        version: VersionNumber | None = None,
    ) -> None:
        super().__init__(entity_id)
        self._tenant_id: TenantId = tenant_id
        self._version: VersionNumber = version if version is not None else VersionNumber(1)
        self._domain_events: list[DomainEvent] = []

    @property
    def tenant_id(self) -> TenantId:
        """Owning tenant identifier for strict multi-tenant isolation."""
        return self._tenant_id

    @property
    def version(self) -> VersionNumber:
        """Optimistic concurrency control aggregate version."""
        return self._version

    def _increment_version(self) -> None:
        self._version = self._version.next()

    def _record_event(self, event: DomainEvent) -> None:
        self._domain_events.append(event)

    def peek_domain_events(self) -> tuple[DomainEvent, ...]:
        """Return a read-only snapshot of queued domain events without clearing them."""
        return tuple(self._domain_events)

    def pull_domain_events(self) -> tuple[DomainEvent, ...]:
        """Drain and return all queued domain events for Unit of Work dispatch."""
        events = tuple(self._domain_events)
        self._domain_events.clear()
        return events
