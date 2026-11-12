"use client";

import Link from "next/link";
import React, { useState } from "react";
import { formatPercentage, formatShortUuid } from "../../../domain/shared/formatters";
import { useDemoBootstrap } from "../../hooks/useDemoBootstrap";

function readField(
  obj: Readonly<Record<string, unknown>> | undefined | null,
  key: string,
): string {
  if (!obj) {
    return "0";
  }
  const val = obj[key];
  if (typeof val === "number") {
    return Number.isInteger(val) ? String(val) : val.toFixed(2);
  }
  if (typeof val === "boolean") {
    return val ? "YES" : "NO";
  }
  if (typeof val === "string") {
    return val;
  }
  return "—";
}

function bandBadgeClass(bandOrTier: string | null): string {
  switch (bandOrTier) {
    case "STRONG_HIRE":
    case "FAST_TRACK":
      return "badge badge-success";
    case "HIRE":
    case "INTERVIEW":
      return "badge badge-info";
    case "BORDERLINE_HOLD":
    case "MANUAL_REVIEW":
      return "badge badge-warning";
    case "NO_HIRE":
    case "GATEKEEPER_REJECTED":
    case "REJECTED":
      return "badge badge-danger";
    default:
      return "badge badge-neutral";
  }
}

export function CommandCenterView(): React.JSX.Element {
  const { state, refreshTelemetry, simulateLiveBatch, setActiveRole } = useDemoBootstrap();
  const { manifest, health, metrics } = state;
  const [simNotice, setSimNotice] = useState<string | null>(null);
  const [simRunning, setSimRunning] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const finops = metrics?.finops_ledger;
  const drift = metrics?.rubric_drift;
  const latency = metrics?.latency_percentiles;
  const llmRuntime = metrics?.llm_runtime;

  const totalCostUsd =
    finops && typeof finops["total_cost_micro_usd"] === "number"
      ? `$${(finops["total_cost_micro_usd"] / 1_000_000).toFixed(4)}`
      : "$0.0000";

  const swaggerHref = state.apiBaseUrl.startsWith("http")
    ? `${state.apiBaseUrl}/docs`
    : "/api/docs";

  const handleCopyId = (label: string, val: string): void => {
    if (typeof globalThis.navigator !== "undefined" && globalThis.navigator.clipboard) {
      void globalThis.navigator.clipboard.writeText(val);
    }
    setCopiedId(label);
    setTimeout(() => setCopiedId((curr) => (curr === label ? null : curr)), 1600);
  };

  const handleSimulateFromHub = async (): Promise<void> => {
    setSimRunning(true);
    setSimNotice(null);
    try {
      const res = await simulateLiveBatch();
      setSimNotice(
        `Queued live simulated batch ${formatShortUuid(res.batch_id)} (${res.file_count} CVs). Open Live Batch Monitor to watch real-time SSE completion.`,
      );
      await refreshTelemetry();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to trigger batch simulation";
      setSimNotice(msg);
    } finally {
      setSimRunning(false);
    }
  };

  return (
    <div className="cmd-container">
      {/* Executive Hero & Live Runtime Banner */}
      <section className="cmd-hero-panel">
        <div className="cmd-status-bar">
          <div className="cmd-status-pills">
            <span
              className={`cmd-pill ${
                state.isBackendOnline ? "cmd-pill--online" : "cmd-pill--offline"
              }`}
            >
              <span
                className={`nav-status-dot ${
                  state.isBackendOnline
                    ? "nav-status-dot--online"
                    : "nav-status-dot--offline"
                }`}
              />
              {state.isBackendOnline
                ? "LIVE BACKEND ONLINE"
                : state.status === "BOOTSTRAPPING"
                  ? "CONNECTING TO ASGI RUNTIME…"
                  : "OFFLINE FALLBACK SHOWCASE MODE"}
            </span>

            <span className="cmd-pill">{manifest.organization_name}</span>

            <span
              className={`cmd-pill ${
                manifest.llm_mode === "live" ? "cmd-pill--llm-live" : "cmd-pill--llm-det"
              }`}
            >
              LLM: {manifest.llm_mode.toUpperCase()} ({manifest.llm_provider} / {manifest.llm_model})
            </span>
          </div>

          <div className="persona-switch" role="group" aria-label="Active JWT Persona">
            <button
              type="button"
              onClick={() => setActiveRole("recruiter")}
              className={`persona-btn ${
                state.activeRole === "recruiter" ? "persona-btn--recruiter" : ""
              }`}
            >
              RECRUITER
            </button>
            <button
              type="button"
              onClick={() => setActiveRole("candidate")}
              className={`persona-btn ${
                state.activeRole === "candidate" ? "persona-btn--candidate" : ""
              }`}
            >
              CANDIDATE
            </button>
          </div>
        </div>

        <div>
          <h1 className="cmd-hero-title">Executive Architecture Command Center</h1>
          <p className="cmd-hero-subtitle">
            End-to-end Clean Architecture platform uniting cryptographic PII tokenization, INT8
            hybrid vector retrieval, stateful 4-plane LangGraph technical interviewing, 60 FPS SSE
            pipeline telemetry, and Empirical Bayes candidate decision calibration.
          </p>
        </div>

        {state.errorMessage && (
          <div className="status-banner danger">
            {state.errorMessage} Run <code className="mono-code">make dev-backend</code> to enable
            live SQLite persistence, WebSockets, and SSE streaming.
          </div>
        )}

        {/* 4-Column KPI Summary Grid */}
        <div className="cmd-kpi-grid">
          <div className="cmd-kpi-card">
            <span className="cmd-kpi-label">Seeded Requisition</span>
            <p className="cmd-kpi-value">{manifest.requisition_title}</p>
            <button
              type="button"
              title={`Copy ${manifest.requisition_id}`}
              onClick={() => handleCopyId("req", manifest.requisition_id)}
              className="cmd-uuid-chip"
            >
              {copiedId === "req" ? "Copied UUID" : formatShortUuid(manifest.requisition_id)}
            </button>
          </div>

          <div className="cmd-kpi-card">
            <span className="cmd-kpi-label">
              Active Session (Turn {manifest.active_session_turn_number})
            </span>
            <p className="cmd-kpi-value cmd-kpi-value--sky">
              Difficulty L{manifest.active_session_difficulty} · IN_PROGRESS
            </p>
            <button
              type="button"
              title={`Copy ${manifest.active_session_id}`}
              onClick={() => handleCopyId("sess", manifest.active_session_id)}
              className="cmd-uuid-chip"
            >
              {copiedId === "sess" ? "Copied UUID" : formatShortUuid(manifest.active_session_id)}
            </button>
          </div>

          <div className="cmd-kpi-card">
            <span className="cmd-kpi-label">Screening Batch</span>
            <p className="cmd-kpi-value cmd-kpi-value--emerald">
              {manifest.shortlisted_candidate_ids.length} Finalists /{" "}
              {manifest.candidate_profile_ids.length} CVs Ingested
            </p>
            <button
              type="button"
              title={`Copy ${manifest.batch_id}`}
              onClick={() => handleCopyId("batch", manifest.batch_id)}
              className="cmd-uuid-chip"
            >
              {copiedId === "batch" ? "Copied UUID" : formatShortUuid(manifest.batch_id)}
            </button>
          </div>

          <div className="cmd-kpi-card">
            <span className="cmd-kpi-label">Evaluation Reports</span>
            <p className="cmd-kpi-value cmd-kpi-value--violet">
              {manifest.report_ids.length} Finalized Reports Ready
            </p>
            <a
              href={swaggerHref}
              target="_blank"
              rel="noreferrer"
              className="cmd-swagger-link"
            >
              Open Swagger UI (/docs) ↗
            </a>
          </div>
        </div>

        {/* 3 Interactive Portal Launch Cards */}
        <div className="cmd-portal-grid">
          <Link href={`/interview/${manifest.active_session_id}`} className="cmd-portal-card">
            <div>
              <span className="cmd-portal-badge cmd-portal-badge--sky">Candidate Surface</span>
              <h2 className="cmd-portal-title">Live Adaptive Interview Portal</h2>
              <p className="cmd-portal-desc">
                Full-duplex WebSocket streaming, active Turn 1 prompt, Socratic follow-up
                branching, 280ms VAD barge-in, and accommodated text mode.
              </p>
            </div>
            <div className="cmd-portal-cta">
              <span>Launch Interview</span>
              <span>→</span>
            </div>
          </Link>

          <Link href={`/recruiter/batches/${manifest.batch_id}`} className="cmd-portal-card">
            <div>
              <span className="cmd-portal-badge cmd-portal-badge--emerald">
                Recruiter Operations
              </span>
              <h2 className="cmd-portal-title">Live Batch Monitor (SSE)</h2>
              <p className="cmd-portal-desc">
                60 FPS requestAnimationFrame SSE coalescing across bulk CV uploads, PII redaction,
                vector indexing, and one-click live worker simulation.
              </p>
            </div>
            <div className="cmd-portal-cta">
              <span>Inspect Screening Batch</span>
              <span>→</span>
            </div>
          </Link>

          <Link
            href={`/recruiter/requisitions/${manifest.requisition_id}/compare`}
            className="cmd-portal-card"
          >
            <div>
              <span className="cmd-portal-badge cmd-portal-badge--violet">
                Decision Workbench
              </span>
              <h2 className="cmd-portal-title">Side-by-Side Comparison Matrix</h2>
              <p className="cmd-portal-desc">
                Posterior calibration across L2..L5 finalists, claim-verification variance, Blind
                Hiring toggle, Evidence Drawer, and audited Recruiter Overrides.
              </p>
            </div>
            <div className="cmd-portal-cta">
              <span>Open Decision Matrix</span>
              <span>→</span>
            </div>
          </Link>
        </div>
      </section>

      {/* Bottom Telemetry + Seeded Candidate Pipeline Grid */}
      <section className="cmd-section-grid">
        <div className="cmd-panel">
          <div className="cmd-section-head">
            <div>
              <p className="eyebrow">Observability &amp; Governance</p>
              <h2 className="cmd-section-title">Live Readiness, FinOps &amp; Rubric Drift</h2>
            </div>
            <div className="button-row">
              <button
                type="button"
                onClick={() => void refreshTelemetry()}
                className="btn btn-secondary"
              >
                Refresh Metrics
              </button>
              <button
                type="button"
                disabled={simRunning || !state.isBackendOnline}
                onClick={() => void handleSimulateFromHub()}
                className="btn btn-emerald"
              >
                {simRunning ? "Queuing…" : "Simulate Live Batch"}
              </button>
            </div>
          </div>

          {simNotice && <div className="status-banner success">{simNotice}</div>}

          <div className="cmd-metric-grid">
            <div className="cmd-metric-card">
              <span className="cmd-metric-label">Readiness (/health/ready)</span>
              <p className="cmd-metric-value cmd-metric-value--emerald">
                {health ? `${health.status.toUpperCase()} (${health.node_id})` : "OFFLINE"}
              </p>
              <p className="cmd-metric-sub">
                SQL: {readField(health?.checks, "sql_database")} · Vector:{" "}
                {readField(health?.checks, "vector_store")}
              </p>
            </div>

            <div className="cmd-metric-card">
              <span className="cmd-metric-label">FinOps Spend (/v1/ops/metrics)</span>
              <p className="cmd-metric-value cmd-metric-value--sky">{totalCostUsd}</p>
              <p className="cmd-metric-sub">
                Tokens: {readField(finops, "total_tokens")} · Runs:{" "}
                {readField(finops, "invocation_count")}
              </p>
            </div>

            <div className="cmd-metric-card">
              <span className="cmd-metric-label">Rubric Drift Monitor</span>
              <p className="cmd-metric-value cmd-metric-value--violet">
                Status:{" "}
                {drift && drift["has_active_drift_alert"] === true ? "DRIFT DETECTED" : "NOMINAL"}
              </p>
              <p className="cmd-metric-sub">
                OTel spans recorded: {readField(latency, "total_spans")}
              </p>
            </div>

            <div className="cmd-metric-card">
              <span className="cmd-metric-label">LLM Provider Telemetry</span>
              <p className="cmd-metric-value cmd-metric-value--amber">
                {readField(llmRuntime, "live_calls")} Live /{" "}
                {readField(llmRuntime, "fallback_calls")} Fallback
              </p>
              <p className="cmd-metric-sub">Engine: {readField(llmRuntime, "provider")}</p>
            </div>
          </div>
        </div>

        <div className="cmd-panel">
          <div className="cmd-section-head">
            <div>
              <p className="eyebrow">Hybrid Screening &amp; Finalist Outcomes</p>
              <h2 className="cmd-section-title">
                Seeded Candidate Pipeline ({manifest.candidate_summaries.length} Profiles)
              </h2>
            </div>
            <Link
              href={`/recruiter/requisitions/${manifest.requisition_id}/compare`}
              className="btn btn-secondary"
            >
              Compare All →
            </Link>
          </div>

          <div className="cmd-pipeline-grid">
            {manifest.candidate_summaries.map((cand) => {
              const badgeValue = cand.recommendation_band ?? cand.tier;
              return (
                <div key={cand.candidate_profile_id} className="cmd-candidate-card">
                  <div>
                    <div className="cmd-candidate-top">
                      <span className="cmd-candidate-name">{cand.display_name}</span>
                      <span className={bandBadgeClass(cand.tier)}>{cand.tier}</span>
                    </div>
                    <p className="cmd-candidate-file">{cand.filename}</p>
                  </div>

                  <div className="cmd-candidate-bottom">
                    <span className="cmd-match-pill">
                      Match {formatPercentage(cand.match_score)}
                    </span>
                    <span className={bandBadgeClass(badgeValue)}>{badgeValue}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>
    </div>
  );
}
