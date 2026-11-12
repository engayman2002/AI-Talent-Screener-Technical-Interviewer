"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useDemoBootstrap } from "../../hooks/useDemoBootstrap";

function readNum(obj: Readonly<Record<string, unknown>> | undefined | null, key: string): string {
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

export function CommandCenterView(): React.JSX.Element {
  const { state, refreshTelemetry, simulateLiveBatch, setActiveRole } = useDemoBootstrap();
  const { manifest, health, metrics } = state;
  const [simNotice, setSimNotice] = useState<string | null>(null);
  const [simRunning, setSimRunning] = useState(false);

  const finops = metrics?.finops_ledger;
  const drift = metrics?.rubric_drift;
  const latency = metrics?.latency_percentiles;
  const llmRuntime = metrics?.llm_runtime;

  const totalCostUsd =
    finops && typeof finops["total_cost_micro_usd"] === "number"
      ? `$${(finops["total_cost_micro_usd"] / 1_000_000).toFixed(4)}`
      : "$0.0000";

  const swaggerHref =
    state.apiBaseUrl.startsWith("http")
      ? `${state.apiBaseUrl}/docs`
      : "/api/docs";

  const handleSimulateFromHub = async (): Promise<void> => {
    setSimRunning(true);
    setSimNotice(null);
    try {
      const res = await simulateLiveBatch();
      setSimNotice(
        `Simulated Batch ${res.batch_id.slice(0, 8)}… queued (${res.file_count} CVs). Open Live Batch Monitor to inspect SSE stream.`,
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
    <main className="mx-auto max-w-6xl space-y-6 p-6 md:p-10">
      <section className="rounded-2xl border border-slate-800 bg-slate-900/90 p-7 shadow-xl">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-300">
            <span>
              {state.isBackendOnline
                ? `LIVE BACKEND ONLINE · ${manifest.organization_name}`
                : state.status === "BOOTSTRAPPING"
                  ? "CONNECTING TO ASGI RUNTIME…"
                  : "OFFLINE FALLBACK SHOWCASE MODE"}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span
              className={`rounded-full border px-3 py-1 font-mono ${
                manifest.llm_mode === "live"
                  ? "border-sky-500/40 bg-sky-500/15 text-sky-200"
                  : "border-amber-500/40 bg-amber-500/15 text-amber-200"
              }`}
            >
              LLM: {manifest.llm_mode.toUpperCase()} ({manifest.llm_provider} / {manifest.llm_model})
            </span>
            <button
              type="button"
              onClick={() => setActiveRole(state.activeRole === "recruiter" ? "candidate" : "recruiter")}
              className="rounded-full border border-slate-700 bg-slate-950 px-3 py-1 text-slate-200 hover:border-sky-500"
            >
              Persona: <strong className="text-sky-300">{state.activeRole.toUpperCase()}</strong>
            </button>
          </div>
        </div>

        <h1 className="mt-4 text-3xl font-bold tracking-tight text-slate-50 md:text-4xl">
          AI Talent Screener &amp; Technical Interviewer
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-slate-300">
          Executive Interactive Architecture Command Center — DDD Bounded Contexts, PII
          Cryptographic Vault, INT8 Hybrid Vector Matching, LangGraph 4-Plane Adaptive Interview
          Engine, RFC 9457 Governance, SSE Coalescing, and Full-Duplex WebSocket Streaming.
        </p>

        {state.errorMessage && (
          <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">
            {state.errorMessage} Start backend with <code>make dev-backend</code> for live SQLite,
            WebSocket &amp; SSE streaming.
          </p>
        )}

        <div className="mt-5 grid gap-3 rounded-xl border border-slate-800 bg-slate-950/70 p-4 text-xs md:grid-cols-4">
          <div>
            <span className="text-slate-400">Seeded Requisition</span>
            <p className="mt-0.5 font-semibold text-slate-100">{manifest.requisition_title}</p>
            <p className="font-mono text-[11px] text-slate-400">{manifest.requisition_id}</p>
          </div>
          <div>
            <span className="text-slate-400">Active Session (Turn {manifest.active_session_turn_number})</span>
            <p className="mt-0.5 font-semibold text-sky-300">
              Difficulty L{manifest.active_session_difficulty} · IN_PROGRESS
            </p>
            <p className="font-mono text-[11px] text-slate-400">{manifest.active_session_id}</p>
          </div>
          <div>
            <span className="text-slate-400">Screening Batch</span>
            <p className="mt-0.5 font-semibold text-emerald-300">
              {manifest.shortlisted_candidate_ids.length} Shortlisted / {manifest.candidate_profile_ids.length} Ingested
            </p>
            <p className="font-mono text-[11px] text-slate-400">{manifest.batch_id}</p>
          </div>
          <div>
            <span className="text-slate-400">Evaluation Reports</span>
            <p className="mt-0.5 font-semibold text-violet-300">
              {manifest.report_ids.length} Finalized Reports Ready
            </p>
            <a
              href={swaggerHref}
              target="_blank"
              rel="noreferrer"
              className="mt-0.5 inline-block font-mono text-[11px] text-sky-400 underline hover:text-sky-300"
            >
              Open Swagger UI (/docs) →
            </a>
          </div>
        </div>

        <div className="mt-6 grid gap-4 md:grid-cols-3">
          <Link
            href={`/interview/${manifest.active_session_id}`}
            className="group rounded-xl border border-slate-800 bg-slate-950/70 p-5 transition hover:border-sky-500/60"
          >
            <p className="text-xs font-semibold uppercase tracking-wider text-sky-400">
              Candidate Surface
            </p>
            <h2 className="mt-1 text-lg font-semibold text-slate-100 group-hover:text-sky-300">
              Live Adaptive Interview Portal →
            </h2>
            <p className="mt-2 text-xs text-slate-400">
              WebSocket /v1/interviews/…/ws, Turn 1 active session, Socratic probing, 280ms
              barge-in &amp; accommodated text mode.
            </p>
          </Link>

          <Link
            href={`/recruiter/batches/${manifest.batch_id}`}
            className="group rounded-xl border border-slate-800 bg-slate-950/70 p-5 transition hover:border-emerald-500/60"
          >
            <p className="text-xs font-semibold uppercase tracking-wider text-emerald-400">
              Recruiter Operations
            </p>
            <h2 className="mt-1 text-lg font-semibold text-slate-100 group-hover:text-emerald-300">
              Live Batch Monitor (SSE) →
            </h2>
            <p className="mt-2 text-xs text-slate-400">
              60 FPS requestAnimationFrame SSE coalescing, task reconciliation, and one-click live
              2-CV background worker simulation.
            </p>
          </Link>

          <Link
            href={`/recruiter/requisitions/${manifest.requisition_id}/compare`}
            className="group rounded-xl border border-slate-800 bg-slate-950/70 p-5 transition hover:border-violet-500/60"
          >
            <p className="text-xs font-semibold uppercase tracking-wider text-violet-400">
              Decision Workbench
            </p>
            <h2 className="mt-1 text-lg font-semibold text-slate-100 group-hover:text-violet-300">
              Side-by-Side Comparison Matrix →
            </h2>
            <p className="mt-2 text-xs text-slate-400">
              L2..L5 finalist evaluations, claim-verification variance, Blind Hiring Mode, and
              audited Recruiter Overrides.
            </p>
          </Link>
        </div>
      </section>

      <section className="grid gap-6 md:grid-cols-2">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
              Live Runtime Readiness, FinOps &amp; Rubric Drift
            </h2>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void refreshTelemetry()}
                className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1 text-xs text-slate-200 hover:border-sky-500"
              >
                Refresh Metrics
              </button>
              <button
                type="button"
                disabled={simRunning || !state.isBackendOnline}
                onClick={() => void handleSimulateFromHub()}
                className="rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {simRunning ? "Queuing…" : "Simulate Live Batch"}
              </button>
            </div>
          </div>

          {simNotice && (
            <p className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-200">
              {simNotice}
            </p>
          )}

          <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3">
              <p className="text-slate-400">Readiness (/health/ready)</p>
              <p className="mt-1 font-mono text-sm font-semibold text-emerald-300">
                {health ? `${health.status.toUpperCase()} (${health.node_id})` : "OFFLINE"}
              </p>
              <p className="mt-1 text-[11px] text-slate-400">
                SQL: {readNum(health?.checks, "sql_database")} · Vector:{" "}
                {readNum(health?.checks, "vector_store")}
              </p>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3">
              <p className="text-slate-400">FinOps Spend (/v1/ops/metrics)</p>
              <p className="mt-1 font-mono text-sm font-semibold text-sky-300">{totalCostUsd}</p>
              <p className="mt-1 text-[11px] text-slate-400">
                Tokens: {readNum(finops, "total_tokens")} · Runs: {readNum(finops, "invocation_count")}
              </p>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3">
              <p className="text-slate-400">Rubric Drift Monitor</p>
              <p className="mt-1 font-mono text-sm font-semibold text-violet-300">
                Alert:{" "}
                {drift && drift["has_active_drift_alert"] === true ? "DRIFT DETECTED" : "NOMINAL"}
              </p>
              <p className="mt-1 text-[11px] text-slate-400">
                Spans recorded: {readNum(latency, "total_spans")}
              </p>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3">
              <p className="text-slate-400">LLM Bridge Calls</p>
              <p className="mt-1 font-mono text-sm font-semibold text-amber-300">
                {readNum(llmRuntime, "live_calls")} Live / {readNum(llmRuntime, "fallback_calls")} Fallback
              </p>
              <p className="mt-1 text-[11px] text-slate-400">
                Provider: {readNum(llmRuntime, "provider")}
              </p>
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
            Seeded Candidate Pipeline ({manifest.candidate_summaries.length} Processed Profiles)
          </h2>
          <div className="mt-4 space-y-2.5">
            {manifest.candidate_summaries.map((cand) => (
              <div
                key={cand.candidate_profile_id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800 bg-slate-950/70 px-4 py-2.5 text-xs"
              >
                <div>
                  <p className="font-semibold text-slate-100">{cand.display_name}</p>
                  <p className="font-mono text-[11px] text-slate-400">{cand.filename}</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-slate-300">
                    Match {(cand.match_score * 100).toFixed(1)}%
                  </span>
                  <span
                    className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
                      cand.tier === "GATEKEEPER_REJECTED"
                        ? "bg-rose-500/20 text-rose-300"
                        : "bg-emerald-500/20 text-emerald-300"
                    }`}
                  >
                    {cand.recommendation_band ?? cand.tier}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}
