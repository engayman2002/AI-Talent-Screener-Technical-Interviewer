"""Quota-Bounded KEDA Worker Pool & WebSocket Gateway Autoscaling Controller."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkerPoolScalingPolicy:
    """SLA + token-ceiling autoscaling parameters per background worker pool."""

    pool_name: str
    min_replicas: int = 1
    max_replicas: int = 20
    concurrency_per_pod: int = 4
    p95_processing_seconds: float = 2.5
    sla_target_seconds: float = 60.0
    scale_up_cooldown_seconds: float = 15.0
    scale_down_stabilization_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class AutoscalingDecision:
    """Deterministic scaling evaluation output including anti-flapping cooldown state."""

    pool_name: str
    previous_replicas: int
    raw_sla_demand_replicas: int
    token_capped_replicas: int
    desired_replicas: int
    applied_replicas: int
    direction: str
    stabilization_held: bool
    reason: str


@dataclass(slots=True)
class _PoolRuntimeState:
    current_replicas: int
    last_scale_up_at: float | None = None
    scale_down_candidate_since: float | None = None


def compute_ws_gateway_replicas(
    *,
    active_sessions: int,
    invited_online: int = 0,
    max_connections_per_pod: int = 150,
    min_replicas: int = 3,
    headroom_utilization_factor: float = 0.70,
) -> int:
    """Compute Multi-AZ WebSocket gateway pod count per Phase 8 Eq. 3.4."""
    if max_connections_per_pod <= 0 or not 0.1 <= headroom_utilization_factor <= 1.0:
        raise ValueError("Invalid WebSocket capacity/headroom parameters.")
    demand = max(0, active_sessions) + 0.5 * max(0, invited_online)
    effective_cap = headroom_utilization_factor * float(max_connections_per_pod)
    return max(min_replicas, math.ceil(demand / effective_cap))


class QueueDepthWorkerAutoscaler:
    """Stateful quota-bounded autoscaler enforcing scale-up/scale-down hysteresis windows."""

    def __init__(self, *, clock_fn: Callable[[], float] | None = None) -> None:
        self._clock: Callable[[], float] = (
            clock_fn if clock_fn is not None else time.monotonic
        )
        self._policies: dict[str, WorkerPoolScalingPolicy] = {}
        self._states: dict[str, _PoolRuntimeState] = {}

    def register_pool(
        self,
        policy: WorkerPoolScalingPolicy,
        *,
        initial_replicas: int | None = None,
    ) -> None:
        if policy.min_replicas < 0 or policy.max_replicas < max(1, policy.min_replicas):
            raise ValueError("Invalid min_replicas / max_replicas range.")
        if policy.concurrency_per_pod <= 0 or policy.p95_processing_seconds <= 0.0:
            raise ValueError("concurrency_per_pod and p95_processing_seconds must be positive.")
        start = (
            initial_replicas if initial_replicas is not None else policy.min_replicas
        )
        start_clamped = max(policy.min_replicas, min(policy.max_replicas, start))
        self._policies[policy.pool_name] = policy
        self._states[policy.pool_name] = _PoolRuntimeState(current_replicas=start_clamped)

    def current_replicas(self, pool_name: str) -> int:
        if pool_name not in self._states:
            raise KeyError(f"Unknown worker pool {pool_name!r}.")
        return self._states[pool_name].current_replicas

    def evaluate_pool(
        self,
        *,
        pool_name: str,
        queue_depth: int,
        elapsed_batch_seconds: float = 0.0,
        max_token_dispatch_per_sec: float | None = None,
    ) -> AutoscalingDecision:
        """Compute bounded target replicas and apply asymmetric cooldown/stabilization."""
        if pool_name not in self._policies:
            raise KeyError(f"Worker pool {pool_name!r} is not registered.")
        policy = self._policies[pool_name]
        state = self._states[pool_name]
        now = self._clock()
        prev = state.current_replicas

        safe_depth = max(0, queue_depth)
        if safe_depth == 0:
            sla_demand = policy.min_replicas
        else:
            remaining_sla = max(
                10.0, policy.sla_target_seconds - max(0.0, elapsed_batch_seconds)
            )
            sla_demand = math.ceil(
                (safe_depth * policy.p95_processing_seconds)
                / (remaining_sla * float(policy.concurrency_per_pod))
            )

        if max_token_dispatch_per_sec is not None and safe_depth > 0:
            token_cap = max(
                1,
                math.ceil(
                    (max(0.01, max_token_dispatch_per_sec) * policy.p95_processing_seconds)
                    / float(policy.concurrency_per_pod)
                ),
            )
            bounded = min(sla_demand, token_cap)
        else:
            token_cap = policy.max_replicas
            bounded = sla_demand

        desired = max(policy.min_replicas, min(policy.max_replicas, bounded))

        if desired > prev:
            # Scale-up path: honor scale_up_cooldown_seconds; reset any scale-down timer
            state.scale_down_candidate_since = None
            in_cooldown = (
                state.last_scale_up_at is not None
                and (now - state.last_scale_up_at) < policy.scale_up_cooldown_seconds
            )
            if in_cooldown:
                return AutoscalingDecision(
                    pool_name=pool_name,
                    previous_replicas=prev,
                    raw_sla_demand_replicas=sla_demand,
                    token_capped_replicas=token_cap,
                    desired_replicas=desired,
                    applied_replicas=prev,
                    direction="HOLD_SCALE_UP_COOLDOWN",
                    stabilization_held=True,
                    reason="Scale-up held within cooldown window.",
                )
            state.current_replicas = desired
            state.last_scale_up_at = now
            return AutoscalingDecision(
                pool_name=pool_name,
                previous_replicas=prev,
                raw_sla_demand_replicas=sla_demand,
                token_capped_replicas=token_cap,
                desired_replicas=desired,
                applied_replicas=desired,
                direction="SCALE_UP",
                stabilization_held=False,
                reason="Scaled up to absorb queue backlog within token ceiling.",
            )

        if desired < prev:
            # Scale-down path: must persist below prev for full stabilization window
            if state.scale_down_candidate_since is None:
                state.scale_down_candidate_since = now
                return AutoscalingDecision(
                    pool_name=pool_name,
                    previous_replicas=prev,
                    raw_sla_demand_replicas=sla_demand,
                    token_capped_replicas=token_cap,
                    desired_replicas=desired,
                    applied_replicas=prev,
                    direction="HOLD_SCALE_DOWN_STABILIZATION",
                    stabilization_held=True,
                    reason="Scale-down stabilization window started.",
                )
            elapsed_down = now - state.scale_down_candidate_since
            if elapsed_down < policy.scale_down_stabilization_seconds:
                return AutoscalingDecision(
                    pool_name=pool_name,
                    previous_replicas=prev,
                    raw_sla_demand_replicas=sla_demand,
                    token_capped_replicas=token_cap,
                    desired_replicas=desired,
                    applied_replicas=prev,
                    direction="HOLD_SCALE_DOWN_STABILIZATION",
                    stabilization_held=True,
                    reason="Waiting for scale-down stabilization window to expire.",
                )
            state.current_replicas = desired
            state.scale_down_candidate_since = None
            return AutoscalingDecision(
                pool_name=pool_name,
                previous_replicas=prev,
                raw_sla_demand_replicas=sla_demand,
                token_capped_replicas=token_cap,
                desired_replicas=desired,
                applied_replicas=desired,
                direction="SCALE_DOWN",
                stabilization_held=False,
                reason="Scaled down after stabilization window elapsed.",
            )

        # Equal replicas: reset pending scale-down candidate timer (prevents flapping)
        state.scale_down_candidate_since = None
        return AutoscalingDecision(
            pool_name=pool_name,
            previous_replicas=prev,
            raw_sla_demand_replicas=sla_demand,
            token_capped_replicas=token_cap,
            desired_replicas=desired,
            applied_replicas=prev,
            direction="STEADY",
            stabilization_held=False,
            reason="Replica count matches desired capacity.",
        )

    def summarize(self) -> dict[str, object]:
        return {
            name: {
                "current_replicas": st.current_replicas,
                "min_replicas": self._policies[name].min_replicas,
                "max_replicas": self._policies[name].max_replicas,
            }
            for name, st in sorted(self._states.items())
        }
