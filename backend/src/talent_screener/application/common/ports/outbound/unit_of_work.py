"""Outbound Application Port for atomic multi-aggregate persistence and event collection."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable

from talent_screener.domain.shared.base import AggregateRoot, DomainEvent
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class TransactionalUnitOfWorkPort(Protocol):
    """Atomic transactional boundary scoping tenant isolation and domain event dispatch."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def begin(self, tenant_id: TenantId) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    def register_aggregate(self, aggregate: AggregateRoot[Any]) -> None: ...

    def collect_pending_events(self) -> tuple[DomainEvent, ...]: ...
