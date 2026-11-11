"""In-process Domain Event Bus reference implementation for choreography & testing."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from talent_screener.application.common.ports.outbound import DomainEventHandlerPort
from talent_screener.domain.shared.base import DomainEvent


class InMemoryDomainEventBus:
    """Deterministic async in-process DomainEventBusPort implementation."""

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[DomainEventHandlerPort[Any]]] = defaultdict(
            list
        )
        self._published_events: list[DomainEvent] = []

    @property
    def published_events(self) -> tuple[DomainEvent, ...]:
        """Read-only history of all events published through this bus."""
        return tuple(self._published_events)

    def subscribe[TEvent: DomainEvent](
        self,
        event_type: type[TEvent],
        handler: DomainEventHandlerPort[TEvent],
    ) -> None:
        """Register an event handler for a specific DomainEvent subclass."""
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        """Publish a single DomainEvent to all subscribed handlers."""
        self._published_events.append(event)
        handlers = list(self._handlers.get(type(event), ()))
        for handler in handlers:
            await handler.handle(event)

    async def publish_all(self, events: Sequence[DomainEvent]) -> None:
        """Publish a sequence of DomainEvents in order."""
        for event in events:
            await self.publish(event)
