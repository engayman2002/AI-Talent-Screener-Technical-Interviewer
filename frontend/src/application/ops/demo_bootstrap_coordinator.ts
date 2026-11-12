/**
 * Demo Bootstrap & Operational Telemetry Coordinator (Frontend Application Layer).
 * Fetches seeded IDs, development JWTs, readiness probes, and live FinOps/Drift metrics
 * via RestApiClientPort with automatic offline deterministic fallback.
 */

import type {
  DemoSeedManifestWire,
  HealthReadyWire,
  OpsMetricsSnapshotWire,
  RestApiClientPort,
  SimulateDemoBatchResponseWire,
} from "../ports/outbound";
import { ObservableStore } from "../shared/store";

export type DemoBootstrapStatus = "IDLE" | "BOOTSTRAPPING" | "ONLINE" | "OFFLINE_FALLBACK";

export interface DemoBootstrapState {
  readonly status: DemoBootstrapStatus;
  readonly isBackendOnline: boolean;
  readonly manifest: DemoSeedManifestWire;
  readonly health: HealthReadyWire | null;
  readonly metrics: OpsMetricsSnapshotWire | null;
  readonly activeRole: "recruiter" | "candidate";
  readonly apiBaseUrl: string;
  readonly wsBaseUrl: string;
  readonly errorMessage: string | null;
}

const OFFLINE_FALLBACK_MANIFEST: DemoSeedManifestWire = {
  tenant_id: "tenant-demo-offline",
  tenant_slug: "acme-ai-engineering",
  organization_name: "Acme AI Engineering (Offline Showcase)",
  requisition_id: "req-staff-backend-01",
  requisition_title: "Senior AI Systems & Full-Stack Engineer",
  batch_id: "batch-demo-01",
  candidate_profile_ids: ["cand-offline-01", "cand-offline-02", "cand-offline-03"],
  shortlisted_candidate_ids: ["cand-offline-01", "cand-offline-02", "cand-offline-03"],
  rejected_candidate_id: "cand-offline-04",
  completed_session_ids: ["sess-offline-01", "sess-offline-02", "sess-offline-03"],
  active_session_id: "sess-demo-01",
  active_session_candidate_id: "cand-offline-01",
  active_session_turn_number: 1,
  active_session_difficulty: 3,
  active_session_question:
    "For SKILL_LANG_PYTHON at L3, how do you enforce backpressure across async workers and SQL transactional outbox writes?",
  active_session_checkpoint_version: "ckpt-offline-v1",
  report_ids: ["rep-offline-01", "rep-offline-02", "rep-offline-03"],
  candidate_summaries: [
    {
      candidate_profile_id: "cand-offline-01",
      display_name: "Elena Vance",
      filename: "elena_vance_staff_ai.pdf",
      match_score: 0.91,
      tier: "SHORTLISTED",
      report_id: "rep-offline-01",
      recommendation_band: "STRONG_HIRE",
    },
    {
      candidate_profile_id: "cand-offline-02",
      display_name: "Marcus Chen",
      filename: "marcus_chen_senior_fs.pdf",
      match_score: 0.84,
      tier: "SHORTLISTED",
      report_id: "rep-offline-02",
      recommendation_band: "HIRE",
    },
    {
      candidate_profile_id: "cand-offline-03",
      display_name: "Priya Nair",
      filename: "priya_nair_mid_ai.pdf",
      match_score: 0.72,
      tier: "SHORTLISTED",
      report_id: "rep-offline-03",
      recommendation_band: "BORDERLINE_HOLD",
    },
  ],
  recruiter_jwt: "",
  candidate_jwt: "",
  llm_mode: "deterministic",
  llm_provider: "deterministic",
  llm_model: "deterministic-offline-v1",
  seeded_at: "",
};

export interface DemoBootstrapCoordinatorDeps {
  readonly api: RestApiClientPort;
  readonly apiBaseUrl: string;
  readonly wsBaseUrl: string;
  readonly setAuthToken: (token: string | null) => void;
}

export class DemoBootstrapCoordinator {
  private readonly api: RestApiClientPort;
  private readonly setAuthToken: (token: string | null) => void;
  private readonly store: ObservableStore<DemoBootstrapState>;
  private bootstrapPromise: Promise<DemoSeedManifestWire | null> | null = null;

  public constructor(deps: DemoBootstrapCoordinatorDeps) {
    this.api = deps.api;
    this.setAuthToken = deps.setAuthToken;
    this.store = new ObservableStore<DemoBootstrapState>({
      status: "IDLE",
      isBackendOnline: false,
      manifest: OFFLINE_FALLBACK_MANIFEST,
      health: null,
      metrics: null,
      activeRole: "recruiter",
      apiBaseUrl: deps.apiBaseUrl,
      wsBaseUrl: deps.wsBaseUrl,
      errorMessage: null,
    });
  }

  public subscribe = (listener: () => void): (() => void) => {
    return this.store.subscribe(listener);
  };

  public getSnapshot = (): DemoBootstrapState => {
    return this.store.getSnapshot();
  };

  public setActiveRole(role: "recruiter" | "candidate"): void {
    const manifest = this.store.getSnapshot().manifest;
    const token = role === "recruiter" ? manifest.recruiter_jwt : manifest.candidate_jwt;
    if (token.trim().length > 0) {
      this.setAuthToken(token);
    }
    this.store.update((prev) => ({ ...prev, activeRole: role }));
  }

  public async bootstrap(forceRefresh = false): Promise<DemoSeedManifestWire | null> {
    if (!forceRefresh && this.bootstrapPromise !== null) {
      return this.bootstrapPromise;
    }

    const task = (async (): Promise<DemoSeedManifestWire | null> => {
      this.store.update((prev) => ({
        ...prev,
        status: prev.isBackendOnline ? prev.status : "BOOTSTRAPPING",
        errorMessage: null,
      }));
      try {
        const manifest = await this.api.fetchDemoManifest();
        const role = this.store.getSnapshot().activeRole;
        const activeJwt =
          role === "recruiter"
            ? manifest.recruiter_jwt
            : manifest.candidate_jwt || manifest.recruiter_jwt;
        if (activeJwt.trim().length > 0) {
          this.setAuthToken(activeJwt);
        }

        const [healthRes, metricsRes] = await Promise.allSettled([
          this.api.fetchHealthReady(),
          this.api.fetchOpsMetrics(),
        ]);

        this.store.update((prev) => ({
          ...prev,
          status: "ONLINE",
          isBackendOnline: true,
          manifest,
          health: healthRes.status === "fulfilled" ? healthRes.value : prev.health,
          metrics: metricsRes.status === "fulfilled" ? metricsRes.value : prev.metrics,
          errorMessage: null,
        }));
        return manifest;
      } catch (err) {
        const message = err instanceof Error ? err.message : "Backend API unreachable";
        this.store.update((prev) => ({
          ...prev,
          status: "OFFLINE_FALLBACK",
          isBackendOnline: false,
          errorMessage: `${message} — running in deterministic offline showcase mode.`,
        }));
        return null;
      }
    })();

    this.bootstrapPromise = task;
    return task;
  }

  public async refreshTelemetry(): Promise<void> {
    try {
      const [healthRes, metricsRes] = await Promise.allSettled([
        this.api.fetchHealthReady(),
        this.api.fetchOpsMetrics(),
      ]);
      this.store.update((prev) => ({
        ...prev,
        status: healthRes.status === "fulfilled" ? "ONLINE" : prev.status,
        isBackendOnline: healthRes.status === "fulfilled",
        health: healthRes.status === "fulfilled" ? healthRes.value : prev.health,
        metrics: metricsRes.status === "fulfilled" ? metricsRes.value : prev.metrics,
      }));
    } catch {
      this.store.update((prev) => ({
        ...prev,
        status: "OFFLINE_FALLBACK",
        isBackendOnline: false,
      }));
    }
  }

  public async simulateLiveBatch(): Promise<SimulateDemoBatchResponseWire> {
    return this.api.simulateDemoBatch();
  }
}
