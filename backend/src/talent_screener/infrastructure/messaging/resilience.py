"""Retry mathematics (exponential backoff + full jitter), circuit breaker, and DLQ routing."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from talent_screener.application.common.dtos import (
    DeadLetterRecordDTO,
    FailureClassification,
)
from talent_screener.application.common.exceptions import (
    ApplicationError,
    ExternalAdapterFailureError,
    GuardrailViolationError,
    IdempotencyConflictError,
    QuotaExceededError,
    ResourceNotFoundError,
    UnauthorizedTenantAccessError,
)
from talent_screener.application.common.ports.outbound import DeadLetterQueuePort
from talent_screener.domain.shared.exceptions import DomainError
from talent_screener.domain.shared.value_objects import TenantId

DeadLetterRecord = DeadLetterRecordDTO

_PERMANENT_APP_EXCEPTIONS = (
    GuardrailViolationError,
    UnauthorizedTenantAccessError,
    ResourceNotFoundError,
    QuotaExceededError,
    IdempotencyConflictError,
)


def classify_failure(exc: Exception) -> FailureClassification:
    """Classify runtime exceptions into retryable TRANSIENT vs immediate-DLQ PERMANENT faults."""
    if isinstance(exc, ExternalAdapterFailureError):
        return FailureClassification.TRANSIENT if exc.retryable else FailureClassification.PERMANENT
    if isinstance(exc, (*_PERMANENT_APP_EXCEPTIONS, DomainError, ValueError, TypeError)):
        return FailureClassification.PERMANENT
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return FailureClassification.TRANSIENT
    return FailureClassification.TRANSIENT


def _extract_error_code(exc: Exception) -> str:
    if isinstance(exc, (ApplicationError, DomainError)):
        return exc.code
    return type(exc).__name__.upper()


@dataclass(frozen=True, slots=True)
class ExponentialBackoffRetryPolicy:
    """Full-jitter exponential backoff: Uniform(0, min(T_max, T_base * 2^k))."""

    base_delay_seconds: float = 1.5
    max_delay_seconds: float = 30.0
    max_retries: int = 4

    def __post_init__(self) -> None:
        if self.base_delay_seconds <= 0.0:
            raise ValueError("base_delay_seconds must be strictly positive.")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds.")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative.")

    def upper_bound_seconds(self, attempt_index: int) -> float:
        """Compute min(T_max, T_base * 2^k) for retry attempt k >= 0."""
        if attempt_index < 0:
            raise ValueError("attempt_index must be >= 0.")
        exp_delay = self.base_delay_seconds * (2.0**attempt_index)
        return float(min(self.max_delay_seconds, exp_delay))

    def compute_sleep_seconds(
        self,
        attempt_index: int,
        *,
        uniform_fn: Callable[[float, float], float] | None = None,
    ) -> float:
        """Compute full-jitter sleep duration T_sleep(k) = Uniform(0, min(T_max, T_base * 2^k))."""
        upper = self.upper_bound_seconds(attempt_index)
        sampler = uniform_fn if uniform_fn is not None else random.SystemRandom().uniform
        return max(0.0, min(upper, float(sampler(0.0, upper))))


class CircuitState(StrEnum):
    """Circuit breaker states protecting upstream extraction and vector providers."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Minimal deterministic circuit breaker guarding transient adapter outages."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        clock_fn: Callable[[], float] | None = None,
    ) -> None:
        self._failure_threshold = max(1, failure_threshold)
        self._recovery_timeout_seconds = max(0.1, recovery_timeout_seconds)
        self._clock = clock_fn if clock_fn is not None else time.monotonic
        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        return self._state

    def ensure_closed(self) -> None:
        if self._state is CircuitState.OPEN:
            assert self._opened_at is not None
            elapsed = self._clock() - self._opened_at
            if elapsed >= self._recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                return
            raise ExternalAdapterFailureError(
                "Circuit breaker is OPEN due to consecutive transient failures.",
                code="CIRCUIT_BREAKER_OPEN",
                retryable=True,
            )

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._state is CircuitState.HALF_OPEN or (
            self._consecutive_failures >= self._failure_threshold
        ):
            self._state = CircuitState.OPEN
            self._opened_at = self._clock()


class InMemoryDeadLetterQueue:
    """In-memory DeadLetterQueuePort retaining terminal failed task records per tenant."""

    def __init__(self) -> None:
        self._records_by_tenant: dict[UUID, list[DeadLetterRecordDTO]] = {}

    async def enqueue(self, record: DeadLetterRecordDTO) -> None:
        self._records_by_tenant.setdefault(record.tenant_id.value, []).append(record)

    async def list_by_tenant(
        self, tenant_id: TenantId
    ) -> tuple[DeadLetterRecordDTO, ...]:
        return tuple(self._records_by_tenant.get(tenant_id.value, ()))


@dataclass(frozen=True, slots=True)
class TaskExecutionOutcome[T]:
    """Outcome summary for resilient task execution including retry delays and DLQ record."""

    succeeded: bool
    result: T | None
    attempt_count: int
    sleep_delays_seconds: tuple[float, ...] = field(default_factory=tuple)
    dead_letter_record: DeadLetterRecordDTO | None = None
    classification: FailureClassification | None = None


class ResilientTaskExecutor:
    """Executes async tasks with full-jitter retry backoff and terminal DLQ routing."""

    def __init__(
        self,
        *,
        retry_policy: ExponentialBackoffRetryPolicy | None = None,
        dlq: DeadLetterQueuePort | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        uniform_fn: Callable[[float, float], float] | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._policy = retry_policy if retry_policy is not None else ExponentialBackoffRetryPolicy()
        self._dlq: DeadLetterQueuePort = dlq if dlq is not None else InMemoryDeadLetterQueue()
        self._sleep = sleep_fn if sleep_fn is not None else asyncio.sleep
        self._uniform = uniform_fn
        self._breaker = circuit_breaker

    async def run_with_dlq[T](
        self,
        *,
        tenant_id: TenantId,
        task_name: str,
        correlation_id: str,
        payload: Mapping[str, object],
        operation: Callable[[], Awaitable[T]],
    ) -> TaskExecutionOutcome[T]:
        """Run operation with transient retries; route permanent/exhausted errors to DLQ."""
        delays: list[float] = []
        attempts = 0
        last_exc: Exception | None = None
        last_class = FailureClassification.PERMANENT

        for attempt_idx in range(self._policy.max_retries + 1):
            attempts += 1
            try:
                if self._breaker is not None:
                    self._breaker.ensure_closed()
                value = await operation()
                if self._breaker is not None:
                    self._breaker.record_success()
                return TaskExecutionOutcome(
                    succeeded=True,
                    result=value,
                    attempt_count=attempts,
                    sleep_delays_seconds=tuple(delays),
                )
            except Exception as exc:
                last_exc = exc
                last_class = classify_failure(exc)
                if last_class is FailureClassification.PERMANENT:
                    break
                if self._breaker is not None and (
                    not isinstance(exc, ExternalAdapterFailureError)
                    or exc.code != "CIRCUIT_BREAKER_OPEN"
                ):
                    self._breaker.record_failure()
                if attempt_idx >= self._policy.max_retries:
                    break
                sleep_duration = self._policy.compute_sleep_seconds(
                    attempt_idx, uniform_fn=self._uniform
                )
                delays.append(sleep_duration)
                await self._sleep(sleep_duration)

        assert last_exc is not None
        record = DeadLetterRecordDTO(
            tenant_id=tenant_id,
            task_name=task_name,
            correlation_id=correlation_id,
            payload=dict(payload),
            error_code=_extract_error_code(last_exc),
            error_message=str(last_exc)[:500],
            attempt_count=attempts,
            classification=last_class,
        )
        await self._dlq.enqueue(record)
        return TaskExecutionOutcome(
            succeeded=False,
            result=None,
            attempt_count=attempts,
            sleep_delays_seconds=tuple(delays),
            dead_letter_record=record,
            classification=last_class,
        )
