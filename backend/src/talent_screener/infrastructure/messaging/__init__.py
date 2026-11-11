"""Messaging & Domain Event Bus infrastructure exports."""

from talent_screener.infrastructure.messaging.in_memory_event_bus import (
    InMemoryDomainEventBus,
)

__all__ = ["InMemoryDomainEventBus"]
