"""Messaging, Resilience & Domain Event Bus infrastructure exports."""

from talent_screener.infrastructure.messaging.distributed_lock import (
    FencingTokenDistributedLockAdapter,
    LockLeaseEntry,
)
from talent_screener.infrastructure.messaging.in_memory_adapters import (
    InMemoryRealtimeProgressPublisher,
    InMemoryTaskQueueDispatcher,
)
from talent_screener.infrastructure.messaging.in_memory_event_bus import (
    InMemoryDomainEventBus,
)
from talent_screener.infrastructure.messaging.resilience import (
    CircuitBreaker,
    CircuitState,
    DeadLetterRecord,
    ExponentialBackoffRetryPolicy,
    InMemoryDeadLetterQueue,
    ResilientTaskExecutor,
    TaskExecutionOutcome,
    classify_failure,
)

__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "DeadLetterRecord",
    "ExponentialBackoffRetryPolicy",
    "FencingTokenDistributedLockAdapter",
    "InMemoryDeadLetterQueue",
    "InMemoryDomainEventBus",
    "InMemoryRealtimeProgressPublisher",
    "InMemoryTaskQueueDispatcher",
    "LockLeaseEntry",
    "ResilientTaskExecutor",
    "TaskExecutionOutcome",
    "classify_failure",
]
