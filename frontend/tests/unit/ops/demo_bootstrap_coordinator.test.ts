import { describe, expect, it } from "vitest";
import { DemoBootstrapCoordinator } from "../../../src/application/ops/demo_bootstrap_coordinator";
import { FetchRestApiClientAdapter, type HttpResponseLike } from "../../../src/infrastructure/http/rest_api_client";

function makeJsonResponse(status: number, body: unknown): HttpResponseLike {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

describe("DemoBootstrapCoordinator & Ops REST Integration", () => {
  it("bootstraps demo manifest, updates JWT token & reads health/metrics", async () => {
    let capturedToken: string | null = null;
    const seenHeaders: Record<string, string>[] = [];

    const api = new FetchRestApiClientAdapter({
      baseUrl: "/api",
      getAuthToken: () => capturedToken,
      fetchFn: async (input, init) => {
        seenHeaders.push(init.headers);
        if (input.endsWith("/v1/ops/demo-manifest")) {
          return makeJsonResponse(200, {
            data: {
              tenant_id: "t-acme",
              tenant_slug: "acme-ai-engineering",
              organization_name: "Acme AI Engineering",
              requisition_id: "req-1",
              requisition_title: "Senior AI Systems Engineer",
              batch_id: "batch-1",
              candidate_profile_ids: ["c1", "c2", "c3"],
              shortlisted_candidate_ids: ["c1", "c2"],
              rejected_candidate_id: "c3",
              completed_session_ids: ["s1"],
              active_session_id: "s-active",
              active_session_candidate_id: "c1",
              active_session_turn_number: 1,
              active_session_difficulty: 3,
              active_session_question: "Explain WAL transactional outbox guarantees.",
              active_session_checkpoint_version: "ckpt-1",
              report_ids: ["r1"],
              candidate_summaries: [],
              recruiter_jwt: "jwt-recruiter-live",
              candidate_jwt: "jwt-candidate-live",
              llm_mode: "deterministic",
              llm_provider: "deterministic",
              llm_model: "deterministic-offline-v1",
              seeded_at: "2026-11-12T00:00:00Z",
            },
            meta: {
              request_id: "req",
              trace_id: "tr",
              tenant_id: "t-acme",
              api_version: "v1",
              timestamp: "2026-11-12T00:00:00Z",
            },
          });
        }
        if (input.endsWith("/health/ready")) {
          return makeJsonResponse(200, {
            status: "ready",
            node_id: "ws-node-1",
            checks: { sql_database: "ok", vector_store: "ok" },
            timestamp: "2026-11-12T00:00:00Z",
          });
        }
        if (input.endsWith("/v1/ops/demo-simulate-batch")) {
          return makeJsonResponse(202, {
            data: {
              batch_id: "batch-sim-99",
              tenant_id: "t-acme",
              requisition_id: "req-1",
              file_count: 2,
              sse_events_path: "/v1/screening-batches/batch-sim-99/events",
              status_path: "/v1/screening-batches/batch-sim-99",
              status: "STARTED",
            },
            meta: {
              request_id: "r2",
              trace_id: "t2",
              tenant_id: "t-acme",
              api_version: "v1",
              timestamp: "2026-11-12T00:00:00Z",
            },
          });
        }
        return makeJsonResponse(200, {
          data: {
            latency_percentiles: { total_spans: 12 },
            finops_ledger: { total_tokens: 4500, total_cost_micro_usd: 1500 },
            rubric_drift: { has_active_drift_alert: false },
          },
          meta: {
            request_id: "req",
            trace_id: "tr",
            tenant_id: "t-acme",
            api_version: "v1",
            timestamp: "2026-11-12T00:00:00Z",
          },
        });
      },
    });

    const coordinator = new DemoBootstrapCoordinator({
      api,
      apiBaseUrl: "/api",
      wsBaseUrl: "ws://127.0.0.1:8000",
      setAuthToken: (t) => {
        capturedToken = t;
      },
    });

    await coordinator.bootstrap();
    const snap = coordinator.getSnapshot();
    expect(snap.status).toBe("ONLINE");
    expect(snap.isBackendOnline).toBe(true);
    expect(snap.manifest.active_session_id).toBe("s-active");
    expect(snap.health?.status).toBe("ready");
    expect(snap.metrics?.finops_ledger["total_tokens"]).toBe(4500);
    expect(capturedToken).toBe("jwt-recruiter-live");

    coordinator.setActiveRole("candidate");
    expect(capturedToken).toBe("jwt-candidate-live");
    expect(coordinator.getSnapshot().activeRole).toBe("candidate");

    const sim = await coordinator.simulateLiveBatch();
    expect(sim.batch_id).toBe("batch-sim-99");
    await coordinator.refreshTelemetry();
    expect(seenHeaders.length).toBeGreaterThanOrEqual(4);
  });

  it("falls back cleanly to offline showcase mode when backend is unreachable", async () => {
    const api = new FetchRestApiClientAdapter({
      baseUrl: "/api",
      getAuthToken: () => null,
      fetchFn: async () => {
        throw new Error("ECONNREFUSED");
      },
    });

    const coordinator = new DemoBootstrapCoordinator({
      api,
      apiBaseUrl: "/api",
      wsBaseUrl: "ws://127.0.0.1:8000",
      setAuthToken: () => undefined,
    });

    const result = await coordinator.bootstrap();
    expect(result).toBeNull();
    const snap = coordinator.getSnapshot();
    expect(snap.status).toBe("OFFLINE_FALLBACK");
    expect(snap.isBackendOnline).toBe(false);
    expect(snap.manifest.active_session_id).toBe("sess-demo-01");
  });
});
