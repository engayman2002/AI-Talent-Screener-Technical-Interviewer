"""Per-Tenant Rate-Limiting Middleware emitting standard RFC rate-limit headers."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.presentation.exceptions import RateLimitExceededError


@dataclass(frozen=True, slots=True)
class RateLimitHeaders:
    """Standard HTTP rate-limit header values."""

    limit: int
    remaining: int
    reset_epoch_seconds: int
    retry_after_seconds: int | None = None

    def to_header_dict(self) -> dict[str, str]:
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_epoch_seconds),
        }
        if self.retry_after_seconds is not None:
            headers["Retry-After"] = str(self.retry_after_seconds)
        return headers


class TenantRateLimiter:
    """Per-tenant fixed-window HTTP rate limiter emitting standard rate-limit headers."""

    def __init__(
        self,
        *,
        default_limit_per_minute: int = 300,
        bucket_limits: Mapping[str, int] | None = None,
        window_seconds: int = 60,
        clock_fn: Callable[[], float] | None = None,
    ) -> None:
        if default_limit_per_minute <= 0 or window_seconds <= 0:
            raise ValueError("Limits and window_seconds must be positive.")
        self._default_limit = default_limit_per_minute
        self._bucket_limits = dict(bucket_limits or {})
        self._window_seconds = window_seconds
        self._clock = clock_fn if clock_fn is not None else time.time
        self._counters: dict[tuple[object, str], tuple[int, int]] = {}

    def check_and_consume(self, *, tenant_id: TenantId, bucket: str) -> RateLimitHeaders:
        """Consume one request unit for ``(tenant_id, bucket)`` or raise RateLimitExceededError."""
        now = self._clock()
        limit = self._bucket_limits.get(bucket, self._default_limit)
        window_idx = int(now // self._window_seconds)
        reset_at = (window_idx + 1) * self._window_seconds
        retry_after = max(1, math.ceil(reset_at - now))

        key = (tenant_id.value, bucket)
        prev_window, prev_count = self._counters.get(key, (-1, 0))
        current_count = prev_count if prev_window == window_idx else 0

        if current_count >= limit:
            raise RateLimitExceededError(
                f"Tenant rate limit ({limit} req/{self._window_seconds}s) exceeded for {bucket}.",
                retry_after_seconds=retry_after,
            )

        updated_count = current_count + 1
        self._counters[key] = (window_idx, updated_count)
        return RateLimitHeaders(
            limit=limit,
            remaining=max(0, limit - updated_count),
            reset_epoch_seconds=reset_at,
        )

    def current_headers_for_error(
        self, *, tenant_id: TenantId, bucket: str, retry_after: int
    ) -> dict[str, str]:
        """Build rate-limit headers suitable for 429 error responses."""
        now = self._clock()
        limit = self._bucket_limits.get(bucket, self._default_limit)
        reset_at = (int(now // self._window_seconds) + 1) * self._window_seconds
        return RateLimitHeaders(
            limit=limit,
            remaining=0,
            reset_epoch_seconds=reset_at,
            retry_after_seconds=retry_after,
        ).to_header_dict()
