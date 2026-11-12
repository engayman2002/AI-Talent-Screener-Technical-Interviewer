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

function bandBadgeClasses(bandOrTier: string | null): string {
  switch (bandOrTier) {
    case "STRONG_HIRE":
    case "FAST_TRACK":
      return "border-emerald-500/40 bg-emerald-500/15 text-emerald-300";
    case "HIRE":
    case "INTERVIEW":
      return "border-sky-500/40 bg-sky-500/15 text-sky-300";
    case "BORDERLINE_HOLD":
    case "MANUAL_REVIEW":
      return "border-amber-500/40 bg-amber-500/15 text-amber-300";
    case "NO_HIRE":
    case "GATEKEEPER_REJECTED":
    case "REJECTED":
      return "border-rose-500/40 bg-rose-500/15 text-rose-300";
    default:
      return "border-slate-700 bg-slate-800/80 text-slate-300";
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
    <div className="space-y-6">
      {/* Executive Hero & Live Runtime Banner */}
      <section className="rounded-2xl border border-slate-800/90 bg-slate-900/90 p-6 shadow-2xl md:p-8">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-2.5">
            <span
              className={`inline-flex items-center gap-2 rounded-full border px-3.5 py-1 text-xs font-semibold ${
                state.isBackendOnline
                  ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
                  : "border-amber-500/40 bg-amber-500/15 text-amber-300"
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full ${
                  state.isBackendOnline
                    ? "bg-emerald-400 shadow-[0_0_8px_#34d399] animate-pulse"
                    : "bg-amber-400"
                }`}
              />
              {state.isBackendOnline
                ? `LIVE BACKEND ONLINE · ${manifest.organization_name}`
                : state.status === "BOOTSTRAPPING"
                  ? "CONNECTING TO ASGI RUNTIME…"
                  : "OFFLINE FALLBACK SHOWCASE MODE"}
            </span>

            <span
              className={`rounded-full border px-3 py-1 font-mono text-xs font-medium ${
                manifest.llm_mode === "live"
                  ? "border-sky-500/40 bg-sky-500/15 text-sky-200"
                  : "border-slate-700 bg-slate-950/80 text-slate-300"
              }`}
            >
              LLM: {manifest.llm_mode.toUpperCase()} ({manifest.llm_provider} / {manifest.llm_model})
            </span>
          </div>

          {/* Segmented Persona Switcher */}
          <div
            className="inline-flex rounded-xl border border-slate-800 bg-slate-950 p-1 text-xs"
            role="group"
            aria-label="Active JWT Persona"
          >
            <button
              type="button"
              onClick={() => setActiveRole("recruiter")}
              className={`rounded-lg px-3.5 py-1.5 font-semibold transition ${
                state.activeRole === "recruiter"
                  ? "bg-sky-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              RECRUITER
            </button>
            <button
              type="button"
              onClick={() => setActiveRole("candidate")}
              className={`rounded-lg px-3.5 py-1.5 font-semibold transition ${
                state.activeRole === "candidate"
                  ? "bg-emerald-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              CANDIDATE
            </button>
          </div>
        </div>

        <div className="mt-5">
          <h1 className="text-2xl font-bold tracking-tight text-slate-50 sm:text-3xl md:text-4xl">
            Executive Architecture Command Center
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate-300">
            End-to-end Clean Architecture platform uniting cryptographic PII tokenization, INT8
            hybrid vector retrieval, stateful 4-plane LangGraph technical interviewing, 60 FPS SSE
            pipeline telemetry, and Empirical Bayes candidate decision calibration.
          </p>
        </div>

        {state.errorMessage && (
          <div className="mt-4 rounded-xl border border-amber-500/35 bg-amber-950/30 px-4 py-2.5 text-xs text-amber-200">
            {state.errorMessage} Run <code className="font-mono text-amber-100">make dev-backend</code>{" "}
            to enable live SQLite persistence, WebSockets, and SSE streaming.
          </div>
        )}

        {/* 4-Column KPI Summary Grid */}
        <div className="mt-6 grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-4">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              Seeded Requisition
            </span>
            <p className="mt-1 truncate text-sm font-semibold text-slate-100">
              {manifest.requisition_title}
            </p>
            <button
              type="button"
              title={`Copy ${manifest.requisition_id}`}
              onClick={() => handleCopyId("req", manifest.requisition_id)}
              className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-slate-800 bg-slate-900 px-2 py-0.5 font-mono text-[11px] text-slate-300 hover:border-sky-500/50 hover:text-sky-300"
            >
              {copiedId === "req" ? "Copied UUID" : formatShortUuid(manifest.requisition_id)}
            </button>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-4">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              Active Session (Turn {manifest.active_session_turn_number})
            </span>
            <p className="mt-1 text-sm font-semibold text-sky-300">
              Difficulty L{manifest.active_session_difficulty} · IN_PROGRESS
            </p>
            <button
              type="button"
              title={`Copy ${manifest.active_session_id}`}
              onClick={() => handleCopyId("sess", manifest.active_session_id)}
              className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-slate-800 bg-slate-900 px-2 py-0.5 font-mono text-[11px] text-slate-300 hover:border-sky-500/50 hover:text-sky-300"
            >
              {copiedId === "sess" ? "Copied UUID" : formatShortUuid(manifest.active_session_id)}
            </button>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-4">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              Screening Batch
            </span>
            <p className="mt-1 text-sm font-semibold text-emerald-300">
              {manifest.shortlisted_candidate_ids.length} Finalists /{" "}
              {manifest.candidate_profile_ids.length} CVs Ingested
            </p>
            <button
              type="button"
              title={`Copy ${manifest.batch_id}`}
              onClick={() => handleCopyId("batch", manifest.batch_id)}
              className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-slate-800 bg-slate-900 px-2 py-0.5 font-mono text-[11px] text-slate-300 hover:border-sky-500/50 hover:text-sky-300"
            >
              {copiedId === "batch" ? "Copied UUID" : formatShortUuid(manifest.batch_id)}
            </button>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-4">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              Evaluation Reports
            </span>
            <p className="mt-1 text-sm font-semibold text-violet-300">
              {manifest.report_ids.length} Finalized Reports Ready
            </p>
            <a
              href={swaggerHref}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-flex items-center gap-1 rounded-md border border-sky-500/30 bg-sky-500/10 px-2.5 py-0.5 font-mono text-[11px] font-semibold text-sky-300 no-underline hover:bg-sky-500/20"
            >
              Open Swagger UI (/docs) ↗
            </a>
          </div>
        </div>

        {/* 3 Interactive Portal Launch Cards */}
        <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-3">
          <Link
            href={`/interview/${manifest.active_session_id}`}
            className="group flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-950/80 p-5 no-underline transition hover:-translate-y-0.5 hover:border-sky-500/60 hover:bg-slate-900"
          >
            <div>
              <span className="inline-block rounded-full border border-sky-500/30 bg-sky-500/10 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-sky-300">
                Candidate Surface
              </span>
              <h2 className="mt-3 text-lg font-semibold text-slate-50 group-hover:text-sky-300">
                Live Adaptive Interview Portal
              </h2>
              <p className="mt-2 text-xs leading-relaxed text-slate-400">
                Full-duplex WebSocket streaming, active Turn 1 prompt, Socratic follow-up
                branching, 280ms VAD barge-in, and accommodated text mode.
              </p>
            </div>
            <div className="mt-5 flex items-center justify-between border-t border-slate-800/80 pt-3 text-xs font-semibold text-sky-400">
              <span>Launch Interview</span>
              <span className="transition group-hover:translate-x-1">→</span>
            </div>
          </Link>

          <Link
            href={`/recruiter/batches/${manifest.batch_id}`}
            className="group flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-950/80 p-5 no-underline transition hover:-translate-y-0.5 hover:border-emerald-500/60 hover:bg-slate-900"
          >
            <div>
              <span className="inline-block rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-emerald-300">
                Recruiter Operations
              </span>
              <h2 className="mt-3 text-lg font-semibold text-slate-50 group-hover:text-emerald-300">
                Live Batch Monitor (SSE)
              </h2>
              <p className="mt-2 text-xs leading-relaxed text-slate-400">
                60 FPS requestAnimationFrame SSE coalescing across bulk CV uploads, PII redaction,
                vector indexing, and one-click live worker simulation.
              </p>
            </div>
            <div className="mt-5 flex items-center justify-between border-t border-slate-800/80 pt-3 text-xs font-semibold text-emerald-400">
              <span>Inspect Screening Batch</span>
              <span className="transition group-hover:translate-x-1">→</span>
            </div>
          </Link>

          <Link
            href={`/recruiter/requisitions/${manifest.requisition_id}/compare`}
            className="group flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-950/80 p-5 no-underline transition hover:-translate-y-0.5 hover:border-violet-500/60 hover:bg-slate-900"
          >
            <div>
              <span className="inline-block rounded-full border border-violet-500/30 bg-violet-500/10 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-violet-300">
                Decision Workbench
              </span>
              <h2 className="mt-3 text-lg font-semibold text-slate-50 group-hover:text-violet-300">
                Side-by-Side Comparison Matrix
              </h2>
              <p className="mt-2 text-xs leading-relaxed text-slate-400">
                Posterior calibration across L2..L5 finalists, claim-verification variance, Blind
                Hiring toggle, Evidence Drawer, and audited Recruiter Overrides.
              </p>
            </div>
            <div className="mt-5 flex items-center justify-between border-t border-slate-800/80 pt-3 text-xs font-semibold text-violet-400">
              <span>Open Decision Matrix</span>
              <span className="transition group-hover:translate-x-1">→</span>
            </div>
          </Link>
        </div>
      </section>

      {/* Bottom Grid: Telemetry/FinOps + Seeded Candidate Pipeline */}
      <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/90 p-6 shadow-xl">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="eyebrow">Observability &amp; Governance</p>
              <h2 className="text-base font-semibold text-slate-100">
                Live Readiness, FinOps &amp; Rubric Drift
              </h2>
            </div>
            <div className="flex flex-wrap gap-2">
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

          {simNotice && (
            <div className="status-banner success">{simNotice}</div>
          )}

          <div className="mt-5 grid grid-cols-1 gap-3.5 sm:grid-cols-2">
            <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-4">
              <span className="text-xs font-medium text-slate-400">Readiness (/health/ready)</span>
              <p className="mt-1 font-mono text-sm font-bold text-emerald-300">
                {health ? `${health.status.toUpperCase()} (${health.node_id})` : "OFFLINE"}
              </p>
              <p className="mt-1.5 text-[11px] text-slate-400">
                SQL: <span className="text-slate-200">{readField(health?.checks, "sql_database")}</span>{" "}
                · Vector:{" "}
                <span className="text-slate-200">{readField(health?.checks, "vector_store")}</span>
              </p>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-4">
              <span className="text-xs font-medium text-slate-400">FinOps Spend (/v1/ops/metrics)</span>
              <p className="mt-1 font-mono text-sm font-bold text-sky-300">{totalCostUsd}</p>
              <p className="mt-1.5 text-[11px] text-slate-400">
                Tokens:{" "}
                <span className="text-slate-200">{readField(finops, "total_tokens")}</span> · Runs:{" "}
                <span className="text-slate-200">{readField(finops, "invocation_count")}</span>
              </p>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-4">
              <span className="text-xs font-medium text-slate-400">Rubric Drift Monitor</span>
              <p className="mt-1 font-mono text-sm font-bold text-violet-300">
                Status:{" "}
                {drift && drift["has_active_drift_alert"] === true ? "DRIFT DETECTED" : "NOMINAL"}
              </p>
              <p className="mt-1.5 text-[11px] text-slate-400">
                OTel spans recorded:{" "}
                <span className="text-slate-200">{readField(latency, "total_spans")}</span>
              </p>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-4">
              <span className="text-xs font-medium text-slate-400">LLM Provider Telemetry</span>
              <p className="mt-1 font-mono text-sm font-bold text-amber-300">
                {readField(llmRuntime, "live_calls")} Live /{" "}
                {readField(llmRuntime, "fallback_calls")} Fallback
              </p>
              <p className="mt-1.5 text-[11px] text-slate-400">
                Engine:{" "}
                <span className="text-slate-200">{readField(llmRuntime, "provider")}</span>
              </p>
            </div>
          </div>
        </div>

        {/* Seeded Candidate Pipeline Card */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900/90 p-6 shadow-xl">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="eyebrow">Hybrid Screening &amp; Finalist Outcomes</p>
              <h2 className="text-base font-semibold text-slate-100">
                Seeded Candidate Pipeline ({manifest.candidate_summaries.length} Profiles)
              </h2>
            </div>
            <Link
              href={`/recruiter/requisitions/${manifest.requisition_id}/compare`}
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1 text-xs font-semibold text-sky-300 no-underline hover:border-sky-500"
            >
              Compare All →
            </Link>
          </div>

          <div className="mt-4 space-y-3">
            {manifest.candidate_summaries.map((cand) => {
              const badgeValue = cand.recommendation_band ?? cand.tier;
              return (
                <div
                  key={cand.candidate_profile_id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-950/80 px-4 py-3 transition hover:border-slate-700"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-slate-100">
                        {cand.display_name}
                      </span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${bandBadgeClasses(cand.tier)}`}
                      >
                        {cand.tier}
                      </span>
                    </div>
                    <p className="mt-0.5 truncate font-mono text-[11px] text-slate-400">
                      {cand.filename}
                    </p>
                  </div>

                  <div className="flex items-center gap-2.5">
                    <span className="rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-1 font-mono text-xs font-semibold text-slate-200">
                      Match {formatPercentage(cand.match_score)}
                    </span>
                    <span
                      className={`rounded-full border px-2.5 py-1 text-[11px] font-bold tracking-wide ${bandBadgeClasses(badgeValue)}`}
                    >
                      {badgeValue}
                    </span>
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
