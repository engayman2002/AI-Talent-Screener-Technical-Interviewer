"""Operational Health Probes (/health/live, /health/ready) and Admin Ops Metrics Controller."""

from __future__ import annotations

from collections.abc import Callable, Mapping

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


class OpsController:
    """Kubernetes liveness/readiness probes and governance:admin observability metrics."""

    def __init__(
        self,
        *,
        node_id: str = "api-node-1",
        readiness_probe_fn: ReadinessProbeFn | None = None,
        metrics_provider_fn: OpsMetricsProviderFn | None = None,
    ) -> None:
        self._node_id = node_id
        self._readiness_probe_fn = readiness_probe_fn
        self._metrics_provider_fn = metrics_provider_fn

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
