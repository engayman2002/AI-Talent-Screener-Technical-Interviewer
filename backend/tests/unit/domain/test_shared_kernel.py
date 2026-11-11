"""Unit tests for the Shared Domain Kernel (base primitives, guards, VOs)."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from talent_screener.domain.shared import (
    AggregateRoot,
    ConfidenceScore,
    DomainError,
    DomainEvent,
    Entity,
    InvalidStateTransitionError,
    InvariantViolationError,
    NormalizedScore,
    SHA256Hash,
    TenantId,
    ValidationError,
    ValueObject,
    VersionNumber,
    clamp,
    ensure,
    generate_uuid7,
    is_uuid7,
    utc_now,
)


class _DummyEntity(Entity[UUID]):
    pass


class _OtherEntity(Entity[UUID]):
    pass


class _DummyAggregate(AggregateRoot[UUID]):
    def trigger(self) -> None:
        self._increment_version()
        self._record_event(_DummyEvent(aggregate_id=self.id, tenant_id=self.tenant_id))


@dataclass(frozen=True, slots=True)
class _DummyEvent(DomainEvent):
    pass


@dataclass(frozen=True, slots=True)
class _PairVO(ValueObject):
    x: int
    y: str


@pytest.mark.unit
def test_uuid7_is_rfc9562_and_strictly_monotonic() -> None:
    generated = [generate_uuid7() for _ in range(50)]
    for uid in generated:
        assert is_uuid7(uid)
        assert uid.version == 7
    assert all(a < b for a, b in itertools.pairwise(generated))


@pytest.mark.unit
def test_entity_identity_equality_and_hashing() -> None:
    uid = generate_uuid7()
    e1 = _DummyEntity(uid)
    e2 = _DummyEntity(uid)
    e3 = _DummyEntity(generate_uuid7())
    other_type = _OtherEntity(uid)

    assert e1 == e2
    assert hash(e1) == hash(e2)
    assert e1 != e3
    assert e1 != other_type
    assert e1 != "not-an-entity"


@pytest.mark.unit
def test_aggregate_root_versioning_and_event_draining() -> None:
    tid = TenantId.generate()
    agg = _DummyAggregate(generate_uuid7(), tenant_id=tid)
    assert agg.version == VersionNumber(1)
    assert agg.peek_domain_events() == ()

    agg.trigger()
    assert agg.version == VersionNumber(2)
    peeked = agg.peek_domain_events()
    assert len(peeked) == 1
    assert peeked[0].event_name == "_DummyEvent"

    pulled = agg.pull_domain_events()
    assert pulled == peeked
    assert agg.pull_domain_events() == ()


@pytest.mark.unit
def test_domain_event_enforces_utc_and_schema_version() -> None:
    tid = TenantId.generate()
    agg_id = generate_uuid7()
    event = _DummyEvent(aggregate_id=agg_id, tenant_id=tid)
    assert event.occurred_at.tzinfo == UTC
    assert event.schema_version == "1.0.0"

    # Non-UTC timezone-aware datetime gets normalized to UTC
    plus_two = timezone(timedelta(hours=2))
    ev_offset = _DummyEvent(
        aggregate_id=agg_id,
        tenant_id=tid,
        occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=plus_two),
    )
    assert ev_offset.occurred_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Naive datetime rejected
    with pytest.raises(ValidationError) as exc_info:
        _DummyEvent(aggregate_id=agg_id, tenant_id=tid, occurred_at=datetime(2026, 1, 1, 12, 0))
    assert exc_info.value.code == "INVALID_EVENT_TIMESTAMP"

    # Nil aggregate ID rejected
    with pytest.raises(ValidationError):
        _DummyEvent(aggregate_id=UUID(int=0), tenant_id=tid)


@pytest.mark.unit
def test_value_object_immutability_and_structural_equality() -> None:
    vo1 = _PairVO(1, "alpha")
    vo2 = _PairVO(1, "alpha")
    assert vo1 == vo2
    assert hash(vo1) == hash(vo2)
    with pytest.raises(AttributeError):
        vo1.x = 2  # type: ignore[misc]


@pytest.mark.unit
def test_shared_value_objects_and_guards() -> None:
    tid = TenantId.from_str(str(generate_uuid7()))
    assert str(tid) == str(tid.value)
    with pytest.raises(InvariantViolationError) as exc_tid:
        TenantId.from_str("not-a-uuid")
    assert exc_tid.value.code == "INVALID_TENANT_ID"

    digest = SHA256Hash.from_bytes(b"hello-world")
    assert len(digest.value) == 64
    assert digest == SHA256Hash(digest.value.upper())
    with pytest.raises(InvariantViolationError):
        SHA256Hash("deadbeef")

    ver = VersionNumber(1)
    assert ver.next() == VersionNumber(2)
    with pytest.raises(ValidationError):
        VersionNumber(0)
    with pytest.raises(ValidationError):
        VersionNumber(True)

    conf = ConfidenceScore(0.85)
    assert float(conf) == pytest.approx(0.85)
    with pytest.raises(ValidationError):
        ConfidenceScore(1.01)
    with pytest.raises(ValidationError):
        ConfidenceScore(math.nan)

    norm = NormalizedScore.from_percentage(75.0)
    assert float(norm) == pytest.approx(0.75)
    assert norm.to_percentage() == pytest.approx(75.0)
    with pytest.raises(ValidationError):
        NormalizedScore(-0.1)

    assert clamp(1.5, 0.0, 1.0) == 1.0
    ensure(True, "ok")
    with pytest.raises(DomainError):
        ensure(False, "boom", code="BOOM")

    err = InvalidStateTransitionError("bad", from_state="A", to_state="B")
    assert err.code == "INVALID_STATE_TRANSITION"
    assert err.details["from_state"] == "A"
    assert utc_now().tzinfo == UTC
