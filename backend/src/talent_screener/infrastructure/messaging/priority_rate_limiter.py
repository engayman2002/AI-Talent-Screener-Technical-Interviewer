"""ADR-006 Priority-Reserved Token Bucket Limiter & Little's Law Backpressure Math."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from talent_screener.application.common.exceptions import QuotaExceededError


class RequestPriorityLane(StrEnum):
    """Execution priority lanes protecting live interviews from bulk CV ingestion bursts."""

    CRITICAL_INTERVIEW = "CRITICAL_INTERVIEW"
    INTERACTIVE_MATCHING = "INTERACTIVE_MATCHING"
    BULK_INGESTION = "BULK_INGESTION"


@dataclass(frozen=True, slots=True)
class RateLimitAcquisitionOutcome:
    """Deterministic outcome of a token-bucket acquisition attempt."""

    granted: bool
    lane: RequestPriorityLane
    tokens_requested: int
    available_after: int
    effective_reserve_floor: int
    retry_after_seconds: float = 0.0
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class BucketSnapshot:
    """Observable point-in-time snapshot of priority-reserved bucket capacity."""

    total_capacity: int
    available_tokens: int
    interview_reserve: int
    matching_reserve: int
    bulk_available: int


def compute_littles_law_concurrency(
    *,
    target_throughput_per_sec: float,
    p95_latency_seconds: float,
    safety_factor: float = 0.25,
) -> int:
    """Compute bounded concurrency L = ceil(lambda * W_p95 * (1 + safety_factor))."""
    if target_throughput_per_sec <= 0.0 or p95_latency_seconds <= 0.0:
        raise ValueError("throughput and p95 latency must be positive.")
    raw = target_throughput_per_sec * p95_latency_seconds * (1.0 + max(0.0, safety_factor))
    return max(1, math.ceil(raw))


def compute_queue_backpressure_delay(
    *,
    queue_depth: int,
    high_water_mark: int = 250,
    critical_limit: int = 600,
    max_pause_seconds: float = 5.0,
) -> float:
    """Compute quadratic queue backpressure delay per Phase 5 Section 2.2 (Eq. 2.2)."""
    if queue_depth <= high_water_mark:
        return 0.0
    if queue_depth >= critical_limit:
        return max_pause_seconds
    span = max(1, critical_limit - high_water_mark)
    ratio = (queue_depth - high_water_mark) / float(span)
    return max_pause_seconds * (ratio**2)


class PriorityReservedTokenBucketLimiter:
    """Shared LLM/API capacity limiter guaranteeing strict interview capacity reservation."""

    def __init__(
        self,
        *,
        total_capacity: int = 100_000,
        interview_reserve: int = 20_000,
        matching_reserve: int = 15_000,
        refill_tokens_per_second: float = 5_000.0,
        clock_fn: Callable[[], float] | None = None,
    ) -> None:
        if total_capacity <= 0:
            raise ValueError("total_capacity must be positive.")
        if interview_reserve < 0 or matching_reserve < 0:
            raise ValueError("Reserves cannot be negative.")
        if interview_reserve + matching_reserve >= total_capacity:
            raise ValueError("Combined reserves must be strictly less than total_capacity.")

        self._total_capacity = total_capacity
        self._interview_reserve = interview_reserve
        self._matching_reserve = matching_reserve
        self._refill_rate = max(0.0, refill_tokens_per_second)
        self._clock = clock_fn if clock_fn is not None else time.monotonic
        self._available: float = float(total_capacity)
        self._last_refill_at: float = self._clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill_at)
        self._last_refill_at = now
        if elapsed > 0.0 and self._refill_rate > 0.0:
            self._available = min(
                float(self._total_capacity),
                self._available + elapsed * self._refill_rate,
            )

    def _reserve_floor_for(self, lane: RequestPriorityLane) -> int:
        if lane is RequestPriorityLane.CRITICAL_INTERVIEW:
            return 0
        if lane is RequestPriorityLane.INTERACTIVE_MATCHING:
            return self._interview_reserve
        return self._interview_reserve + self._matching_reserve

    def snapshot(self) -> BucketSnapshot:
        """Refill and return current available token headroom per priority tier."""
        self._refill()
        avail = int(self._available)
        bulk_floor = self._interview_reserve + self._matching_reserve
        return BucketSnapshot(
            total_capacity=self._total_capacity,
            available_tokens=avail,
            interview_reserve=self._interview_reserve,
            matching_reserve=self._matching_reserve,
            bulk_available=max(0, avail - bulk_floor),
        )

    def try_acquire(
        self, *, lane: RequestPriorityLane, tokens: int
    ) -> RateLimitAcquisitionOutcome:
        """Attempt non-blocking acquisition honoring strict lower-tier reservation floors."""
        if tokens <= 0:
            raise ValueError("tokens must be positive.")
        self._refill()
        floor = self._reserve_floor_for(lane)
        projected = self._available - float(tokens)
        if projected < float(floor):
            deficit = float(floor) - projected
            retry_after = (
                round(deficit / self._refill_rate, 3) if self._refill_rate > 0.0 else 1.0
            )
            reason = (
                "BULK_BACKPRESSURE_RESERVED_CAPACITY"
                if lane is RequestPriorityLane.BULK_INGESTION
                else "PRIORITY_CAPACITY_EXHAUSTED"
            )
            return RateLimitAcquisitionOutcome(
                granted=False,
                lane=lane,
                tokens_requested=tokens,
                available_after=int(self._available),
                effective_reserve_floor=floor,
                retry_after_seconds=max(0.01, retry_after),
                reason_code=reason,
            )

        self._available = projected
        return RateLimitAcquisitionOutcome(
            granted=True,
            lane=lane,
            tokens_requested=tokens,
            available_after=int(self._available),
            effective_reserve_floor=floor,
            retry_after_seconds=0.0,
            reason_code=None,
        )

    def acquire_or_raise(
        self, *, lane: RequestPriorityLane, tokens: int
    ) -> RateLimitAcquisitionOutcome:
        """Acquire tokens or fail closed with ``QuotaExceededError(code='PRIORITY_RATE_LIMIT')``."""
        outcome = self.try_acquire(lane=lane, tokens=tokens)
        if not outcome.granted:
            raise QuotaExceededError(
                (
                    f"Rate limit capacity exhausted for priority lane {lane.value} "
                    f"(requested={tokens}, available={outcome.available_after})."
                ),
                code="PRIORITY_RATE_LIMIT_EXCEEDED",
                metadata={
                    "lane": lane.value,
                    "retry_after_seconds": outcome.retry_after_seconds,
                    "reason_code": outcome.reason_code or "RATE_LIMITED",
                },
            )
        return outcome
