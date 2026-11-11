"""Sliding-window FinOps TokenBudgetEnforcerPort adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from talent_screener.application.common.exceptions import QuotaExceededError
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import TenantId


@dataclass(frozen=True, slots=True)
class TurnTokenUsageRecord:
    """Immutable per-turn token consumption and FinOps micro-USD ledger entry."""

    tenant_id: TenantId
    session_id: UUID
    tokens_used: int
    cost_micro_usd: int
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class SessionBudgetStatus:
    """Current tenant-scoped session budget consumption snapshot."""

    tenant_id: TenantId
    session_id: UUID
    total_tokens_consumed: int
    window_tokens_consumed: int
    total_cost_micro_usd: int
    turn_count: int


class SlidingWindowTokenBudgetEnforcerAdapter:
    """Enforces session hard ceilings plus sliding-window spike guards (ADR-013)."""

    def __init__(
        self,
        *,
        window_seconds: int = 60,
        max_tokens_per_window: int | None = None,
        micro_usd_per_1k_tokens: int = 2_000,
    ) -> None:
        self._window = timedelta(seconds=max(1, window_seconds))
        self._max_tokens_per_window = max_tokens_per_window
        self._micro_usd_per_1k_tokens = max(0, micro_usd_per_1k_tokens)
        self._records_by_session: dict[tuple[UUID, UUID], list[TurnTokenUsageRecord]] = {}

    async def ensure_within_budget(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        requested_tokens: int,
        session_limit: int,
    ) -> None:
        """Fail closed with ``QuotaExceededError(code='TOKEN_BUDGET_EXCEEDED')`` when over cap."""
        if requested_tokens < 0:
            raise ValueError("requested_tokens cannot be negative.")
        key = (tenant_id.value, session_id)
        records = self._records_by_session.get(key, ())
        total_consumed = sum(r.tokens_used for r in records)

        if total_consumed + requested_tokens > session_limit:
            raise QuotaExceededError(
                (
                    f"Session token ceiling {session_limit} exceeded "
                    f"(consumed={total_consumed}, requested={requested_tokens})."
                ),
                code="TOKEN_BUDGET_EXCEEDED",
                metadata={
                    "session_id": str(session_id),
                    "total_consumed": total_consumed,
                    "requested_tokens": requested_tokens,
                    "session_limit": session_limit,
                },
            )

        if self._max_tokens_per_window is not None:
            cutoff = utc_now() - self._window
            window_used = sum(r.tokens_used for r in records if r.recorded_at >= cutoff)
            if window_used + requested_tokens > self._max_tokens_per_window:
                raise QuotaExceededError(
                    (
                        f"Sliding-window token burst ceiling {self._max_tokens_per_window} "
                        f"exceeded (window_used={window_used}, requested={requested_tokens})."
                    ),
                    code="TOKEN_BURST_BUDGET_EXCEEDED",
                    metadata={
                        "window_used": window_used,
                        "max_tokens_per_window": self._max_tokens_per_window,
                    },
                )

    async def record_consumption(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        tokens_used: int,
    ) -> int:
        """Append token ledger entry and return cumulative session total."""
        if tokens_used < 0:
            raise ValueError("tokens_used cannot be negative.")
        key = (tenant_id.value, session_id)
        cost = (tokens_used * self._micro_usd_per_1k_tokens) // 1000
        record = TurnTokenUsageRecord(
            tenant_id=tenant_id,
            session_id=session_id,
            tokens_used=tokens_used,
            cost_micro_usd=cost,
            recorded_at=utc_now(),
        )
        history = self._records_by_session.setdefault(key, [])
        history.append(record)
        return sum(item.tokens_used for item in history)

    def get_session_status(
        self, tenant_id: TenantId, session_id: UUID
    ) -> SessionBudgetStatus:
        """Return immutable budget telemetry snapshot for FinOps monitoring."""
        records = self._records_by_session.get((tenant_id.value, session_id), ())
        cutoff = utc_now() - self._window
        return SessionBudgetStatus(
            tenant_id=tenant_id,
            session_id=session_id,
            total_tokens_consumed=sum(r.tokens_used for r in records),
            window_tokens_consumed=sum(r.tokens_used for r in records if r.recorded_at >= cutoff),
            total_cost_micro_usd=sum(r.cost_micro_usd for r in records),
            turn_count=len(records),
        )

    def get_tenant_total_tokens(self, tenant_id: TenantId) -> int:
        """Aggregate total tokens consumed across all sessions belonging to a tenant."""
        return sum(
            record.tokens_used
            for (t_id, _), records in self._records_by_session.items()
            if t_id == tenant_id.value
            for record in records
        )
