"""ADR-013 Multi-Tenant FinOps Cost Attribution Ledger & Semantic Model Tier Router."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from talent_screener.application.common.exceptions import QuotaExceededError
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import TenantId


class ModelTier(StrEnum):
    """Dynamic model cost routing tiers."""

    PRIMARY_FRONTIER_MODEL = "PRIMARY_FRONTIER_MODEL"
    COST_OPTIMIZED_FAST_MODEL = "COST_OPTIMIZED_FAST_MODEL"


@dataclass(frozen=True, slots=True)
class ModelPricingSpec:
    """Per-1k token pricing in integer micro-USD (1 USD = 1,000,000 micro-USD)."""

    model_id: str
    tier: ModelTier
    cached_input_micro_usd_per_1k: int
    uncached_input_micro_usd_per_1k: int
    output_micro_usd_per_1k: int


DEFAULT_FRONTIER_MODEL = ModelPricingSpec(
    model_id="openai/gpt-4o-2024-11-20",
    tier=ModelTier.PRIMARY_FRONTIER_MODEL,
    cached_input_micro_usd_per_1k=1_250,
    uncached_input_micro_usd_per_1k=2_500,
    output_micro_usd_per_1k=10_000,
)

DEFAULT_FAST_MODEL = ModelPricingSpec(
    model_id="openai/gpt-4o-mini",
    tier=ModelTier.COST_OPTIMIZED_FAST_MODEL,
    cached_input_micro_usd_per_1k=75,
    uncached_input_micro_usd_per_1k=150,
    output_micro_usd_per_1k=600,
)


@dataclass(frozen=True, slots=True)
class FinOpsUsageEntry:
    """Immutable LLM invocation attribution entry mapped to tenant/requisition/session/node."""

    tenant_id: TenantId
    requisition_id: UUID | None
    session_id: UUID
    node_name: str
    model_id: str
    tier: ModelTier
    cached_input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    cost_micro_usd: int
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class ModelRoutingDecision:
    """Tier selection outcome honoring 80% soft downgrade & 100% hard circuit breaker."""

    selected_tier: ModelTier
    selected_model_id: str
    session_utilization_ratio: float
    tenant_utilization_ratio: float
    downgraded_due_to_soft_cap: bool
    reason: str


class MultiTenantFinOpsController:
    """Tracks multi-tenant FinOps unit economics and routes LLM tiers across budgets."""

    def __init__(
        self,
        *,
        session_token_quota: int = 45_000,
        default_tenant_token_quota: int = 2_000_000,
        soft_downgrade_ratio: float = 0.80,
        frontier_spec: ModelPricingSpec = DEFAULT_FRONTIER_MODEL,
        fast_spec: ModelPricingSpec = DEFAULT_FAST_MODEL,
    ) -> None:
        if session_token_quota <= 0 or default_tenant_token_quota <= 0:
            raise ValueError("Token quotas must be strictly positive.")
        if not 0.0 < soft_downgrade_ratio < 1.0:
            raise ValueError("soft_downgrade_ratio must lie strictly in (0, 1).")

        self._session_token_quota = session_token_quota
        self._default_tenant_token_quota = default_tenant_token_quota
        self._soft_ratio = soft_downgrade_ratio
        self._frontier = frontier_spec
        self._fast = fast_spec
        self._tenant_quotas: dict[UUID, int] = {}
        self._entries: list[FinOpsUsageEntry] = []

    def configure_tenant_quota(self, tenant_id: TenantId, monthly_token_quota: int) -> None:
        if monthly_token_quota <= 0:
            raise ValueError("monthly_token_quota must be strictly positive.")
        self._tenant_quotas[tenant_id.value] = monthly_token_quota

    def get_tenant_quota(self, tenant_id: TenantId) -> int:
        return self._tenant_quotas.get(tenant_id.value, self._default_tenant_token_quota)

    def session_token_total(self, *, tenant_id: TenantId, session_id: UUID) -> int:
        return sum(
            e.cached_input_tokens + e.uncached_input_tokens + e.output_tokens
            for e in self._entries
            if e.tenant_id.value == tenant_id.value and e.session_id == session_id
        )

    def tenant_token_total(self, tenant_id: TenantId) -> int:
        return sum(
            e.cached_input_tokens + e.uncached_input_tokens + e.output_tokens
            for e in self._entries
            if e.tenant_id.value == tenant_id.value
        )

    def tenant_cost_micro_usd(self, tenant_id: TenantId) -> int:
        return sum(e.cost_micro_usd for e in self._entries if e.tenant_id.value == tenant_id.value)

    def select_model_tier(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        projected_tokens: int = 0,
        session_token_quota: int | None = None,
    ) -> ModelRoutingDecision:
        """Select frontier vs. cost-optimized model or trip 100% FinOps circuit breaker."""
        if projected_tokens < 0:
            raise ValueError("projected_tokens cannot be negative.")
        s_quota = (
            session_token_quota
            if session_token_quota is not None
            else self._session_token_quota
        )
        t_quota = self.get_tenant_quota(tenant_id)

        s_used = self.session_token_total(tenant_id=tenant_id, session_id=session_id)
        t_used = self.tenant_token_total(tenant_id)

        s_proj_ratio = (s_used + projected_tokens) / float(s_quota)
        t_proj_ratio = (t_used + projected_tokens) / float(t_quota)
        # Hard cap checks current or projected exhaustion >= 100%
        over_session = s_used >= s_quota or (s_used + projected_tokens) > s_quota
        over_tenant = t_used >= t_quota or (t_used + projected_tokens) > t_quota
        if over_session or over_tenant:
            raise QuotaExceededError(
                (
                    f"FinOps hard budget exceeded for tenant={tenant_id.value} "
                    f"session={session_id} (session={s_used}/{s_quota}, tenant={t_used}/{t_quota})."
                ),
                code="FINOPS_HARD_BUDGET_EXCEEDED",
                metadata={
                    "tenant_id": str(tenant_id.value),
                    "session_id": str(session_id),
                    "session_used": s_used,
                    "session_quota": s_quota,
                    "tenant_used": t_used,
                    "tenant_quota": t_quota,
                },
            )

        max_util = max(s_used / float(s_quota), t_used / float(t_quota), s_proj_ratio, t_proj_ratio)
        if max_util >= self._soft_ratio:
            return ModelRoutingDecision(
                selected_tier=ModelTier.COST_OPTIMIZED_FAST_MODEL,
                selected_model_id=self._fast.model_id,
                session_utilization_ratio=round(s_used / float(s_quota), 4),
                tenant_utilization_ratio=round(t_used / float(t_quota), 4),
                downgraded_due_to_soft_cap=True,
                reason="SOFT_BUDGET_THRESHOLD_80_PCT",
            )

        return ModelRoutingDecision(
            selected_tier=ModelTier.PRIMARY_FRONTIER_MODEL,
            selected_model_id=self._frontier.model_id,
            session_utilization_ratio=round(s_used / float(s_quota), 4),
            tenant_utilization_ratio=round(t_used / float(t_quota), 4),
            downgraded_due_to_soft_cap=False,
            reason="WITHIN_PRIMARY_BUDGET",
        )

    def record_usage(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        node_name: str,
        cached_input_tokens: int,
        uncached_input_tokens: int,
        output_tokens: int,
        requisition_id: UUID | None = None,
        pricing: ModelPricingSpec | None = None,
    ) -> FinOpsUsageEntry:
        """Compute integer micro-USD cost and append FinOps ledger entry."""
        if cached_input_tokens < 0 or uncached_input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts must be non-negative.")

        if pricing is not None:
            spec = pricing
        else:
            decision = self.select_model_tier(
                tenant_id=tenant_id,
                session_id=session_id,
                projected_tokens=0,
            )
            spec = (
                self._fast
                if decision.selected_tier is ModelTier.COST_OPTIMIZED_FAST_MODEL
                else self._frontier
            )

        cost_micro = (
            (cached_input_tokens * spec.cached_input_micro_usd_per_1k)
            + (uncached_input_tokens * spec.uncached_input_micro_usd_per_1k)
            + (output_tokens * spec.output_micro_usd_per_1k)
        ) // 1000

        entry = FinOpsUsageEntry(
            tenant_id=tenant_id,
            requisition_id=requisition_id,
            session_id=session_id,
            node_name=node_name,
            model_id=spec.model_id,
            tier=spec.tier,
            cached_input_tokens=cached_input_tokens,
            uncached_input_tokens=uncached_input_tokens,
            output_tokens=output_tokens,
            cost_micro_usd=cost_micro,
            recorded_at=utc_now(),
        )
        self._entries.append(entry)
        return entry

    def summarize_ledger(self, *, tenant_id: TenantId | None = None) -> dict[str, object]:
        """Export structured multi-tenant FinOps cost & prefix-cache efficiency summary."""
        relevant = [
            e
            for e in self._entries
            if tenant_id is None or e.tenant_id.value == tenant_id.value
        ]
        total_cached = sum(e.cached_input_tokens for e in relevant)
        total_uncached = sum(e.uncached_input_tokens for e in relevant)
        total_output = sum(e.output_tokens for e in relevant)
        total_cost = sum(e.cost_micro_usd for e in relevant)
        denom = total_cached + total_uncached
        cache_hit_ratio = round(total_cached / float(denom), 4) if denom > 0 else 0.0

        by_tenant: dict[str, dict[str, int]] = {}
        for entry in relevant:
            t_key = str(entry.tenant_id.value)
            bucket = by_tenant.setdefault(
                t_key,
                {"tokens": 0, "cost_micro_usd": 0, "invocations": 0},
            )
            bucket["tokens"] += (
                entry.cached_input_tokens + entry.uncached_input_tokens + entry.output_tokens
            )
            bucket["cost_micro_usd"] += entry.cost_micro_usd
            bucket["invocations"] += 1

        return {
            "invocation_count": len(relevant),
            "cached_input_tokens": total_cached,
            "uncached_input_tokens": total_uncached,
            "output_tokens": total_output,
            "total_tokens": total_cached + total_uncached + total_output,
            "prefix_cache_hit_ratio": cache_hit_ratio,
            "total_cost_micro_usd": total_cost,
            "total_cost_usd": round(total_cost / 1_000_000.0, 6),
            "by_tenant": by_tenant,
        }
