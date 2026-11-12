"""Operational Health Probes (/health/live, /health/ready) and Admin Ops Metrics Controller."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping

from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.presentation.middleware import PrincipalContext
from talent_screener.presentation.schemas import (
    ApiResponseEnvelope,
    HttpRequest,
    HttpResponse,
    ResponseMeta,
)

ReadinessProbeFn = Callable[[], tuple[bool, Mapping[str, object]]]
OpsMetricsProviderFn = Callable[[], Mapping[str, object]]
DemoManifestProviderFn = Callable[[], Mapping[str, object] | Awaitable[Mapping[str, object]]]
DemoSimulateBatchFn = Callable[[], Mapping[str, object] | Awaitable[Mapping[str, object]]]


class OpsController:
    """Kubernetes liveness/readiness probes and governance:admin observability metrics."""

    def __init__(
        self,
        *,
        node_id: str = "api-node-1",
        readiness_probe_fn: ReadinessProbeFn | None = None,
        metrics_provider_fn: OpsMetricsProviderFn | None = None,
        demo_manifest_provider_fn: DemoManifestProviderFn | None = None,
        demo_simulate_batch_fn: DemoSimulateBatchFn | None = None,
        demo_mode_enabled: bool = True,
    ) -> None:
        self._node_id = node_id
        self._readiness_probe_fn = readiness_probe_fn
        self._metrics_provider_fn = metrics_provider_fn
        self._demo_manifest_provider_fn = demo_manifest_provider_fn
        self._demo_simulate_batch_fn = demo_simulate_batch_fn
        self._demo_mode_enabled = demo_mode_enabled

    async def liveness(self, request: HttpRequest) -> HttpResponse:
        """Return HTTP 200 OK confirming process event loop responsiveness."""
        _ = request
        return HttpResponse(
            status_code=200,
            body={
                "status": "alive",
                "node_id": self._node_id,
                "timestamp": utc_now().isoformat(),
            },
        )

    async def readiness(self, request: HttpRequest) -> HttpResponse:
        """Verify SQL, vector store, checkpointer & drain state; return 200 or 503."""
        _ = request
        if self._readiness_probe_fn is not None:
            is_ready, checks = self._readiness_probe_fn()
        else:
            is_ready, checks = (
                True,
                {
                    "sql_database": "ok",
                    "vector_store": "ok",
                    "checkpointer": "ok",
                    "draining": False,
                },
            )

        now_iso = utc_now().isoformat()
        if is_ready:
            return HttpResponse(
                status_code=200,
                body={
                    "status": "ready",
                    "node_id": self._node_id,
                    "checks": dict(checks),
                    "timestamp": now_iso,
                },
            )

        return HttpResponse(
            status_code=503,
            body={
                "type": "urn:ai-screener:error:upstream_dependency",
                "title": "Service Unavailable",
                "status": 503,
                "detail": "Service readiness dependencies are degraded or node is draining.",
                "error_code": "SERVICE_NOT_READY",
                "node_id": self._node_id,
                "checks": dict(checks),
                "timestamp": now_iso,
            },
            headers={"Retry-After": "5"},
        )

    async def metrics(
        self,
        request: HttpRequest,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        """Return latency percentiles, FinOps token/cost ledger & Rubric Drift stats."""
        _ = (request, principal)
        if self._metrics_provider_fn is not None:
            snapshot = dict(self._metrics_provider_fn())
        else:
            snapshot = {
                "latency_percentiles": {"total_spans": 0, "overall_latency_ms": {}},
                "finops_ledger": {"total_tokens": 0, "total_cost_micro_usd": 0},
                "rubric_drift": {"has_active_drift_alert": False},
            }

        envelope = ApiResponseEnvelope(data=snapshot, meta=meta)
        return HttpResponse(status_code=200, body=envelope.to_dict())

    async def demo_manifest(
        self,
        request: HttpRequest,
        meta: ResponseMeta,
    ) -> HttpResponse:
        """Return seeded interactive demo workspace IDs, sample reports & demo JWTs."""
        _ = request
        if not self._demo_mode_enabled or self._demo_manifest_provider_fn is None:
            return HttpResponse(
                status_code=404,
                body={
                    "type": "urn:ai-screener:error:routing",
                    "title": "Demo Mode Disabled",
                    "status": 404,
                    "detail": "Interactive demo manifest is disabled in this runtime.",
                    "error_code": "DEMO_MODE_DISABLED",
                    "trace_id": meta.trace_id,
                    "timestamp": utc_now().isoformat(),
                },
            )
        maybe_awaitable = self._demo_manifest_provider_fn()
        resolved = (
            await maybe_awaitable if inspect.isawaitable(maybe_awaitable) else maybe_awaitable
        )
        envelope = ApiResponseEnvelope(data=dict(resolved), meta=meta)
        return HttpResponse(status_code=200, body=envelope.to_dict())

    async def simulate_demo_batch(
        self,
        request: HttpRequest,
        meta: ResponseMeta,
    ) -> HttpResponse:
        """Trigger a background simulated CV screening batch for live SSE telemetry streaming."""
        _ = request
        if not self._demo_mode_enabled or self._demo_simulate_batch_fn is None:
            return HttpResponse(
                status_code=404,
                body={
                    "type": "urn:ai-screener:error:routing",
                    "title": "Demo Batch Simulation Disabled",
                    "status": 404,
                    "detail": "Interactive batch simulation is disabled in this runtime.",
                    "error_code": "DEMO_MODE_DISABLED",
                    "trace_id": meta.trace_id,
                    "timestamp": utc_now().isoformat(),
                },
            )
        maybe_awaitable = self._demo_simulate_batch_fn()
        resolved = (
            await maybe_awaitable if inspect.isawaitable(maybe_awaitable) else maybe_awaitable
        )
        envelope = ApiResponseEnvelope(data=dict(resolved), meta=meta)
        return HttpResponse(status_code=202, body=envelope.to_dict())
