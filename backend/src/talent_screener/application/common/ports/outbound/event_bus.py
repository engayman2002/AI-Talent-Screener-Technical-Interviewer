"""Outbound Application Ports for decoupled Domain Event publication and handling."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from talent_screener.domain.shared.base import DomainEvent


@runtime_checkable
class DomainEventHandlerPort[TEvent: DomainEvent](Protocol):
    """Contract for asynchronous in-process or outbox Domain Event subscribers."""

    async def handle(self, event: TEvent) -> None: ...


@runtime_checkable
class DomainEventBusPort(Protocol):
    """Cross-context event choreography bus decoupling producers from consumers."""

    def subscribe[TEvent: DomainEvent](
        self,
        event_type: type[TEvent],
        handler: DomainEventHandlerPort[TEvent],
    ) -> None: ...

    async def publish(self, event: DomainEvent) -> None: ...

    async def publish_all(self, events: Sequence[DomainEvent]) -> None: ...
