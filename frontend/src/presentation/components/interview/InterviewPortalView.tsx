"use client";

import React, { useEffect, useRef, useState } from "react";
import { formatShortUuid } from "../../../domain/shared/formatters";
import { useDemoBootstrap } from "../../hooks/useDemoBootstrap";
import { useInterviewController } from "../../hooks/useInterviewController";

export interface InterviewPortalViewProps {
  readonly sessionId: string;
}

const STATE_BADGE_CLASS: Readonly<Record<string, string>> = {
  IDLE: "badge badge-neutral",
  AUTHENTICATING: "badge badge-info",
  CONNECTING: "badge badge-info",
  READY: "badge badge-success",
  INTERVIEWER_SPEAKING: "badge badge-accent",
  CANDIDATE_LISTENING: "badge badge-success",
  CANDIDATE_SPEAKING: "badge badge-success",
  BARGE_IN_INTERRUPTING: "badge badge-warning",
  SUBMITTING_TURN: "badge badge-info",
  INTERVIEWER_THINKING: "badge badge-info",
  RECONNECTING: "badge badge-warning",
  COMPLETED: "badge badge-success",
  TERMINATED_ERROR: "badge badge-danger",
};

function formatRemainingTime(totalSeconds: number): string {
  const clamped = Math.max(0, Math.floor(totalSeconds));
  const mins = Math.floor(clamped / 60);
  const secs = clamped % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

export function InterviewPortalView({
  sessionId,
}: InterviewPortalViewProps): React.JSX.Element {
  const {
    snapshot,
    jitterCushionMs,
    startSession,
    triggerBargeIn,
    markTurnInputReady,
    updateSupplementalText,
    submitUtterance,
    setAccommodatedTextMode,
    terminateSession,
  } = useInterviewController();
  const { state: demoState } = useDemoBootstrap();

  const [ticketToken, setTicketToken] = useState("candidate-demo-ticket");
  const [wsUrl, setWsUrl] = useState(
    `${demoState.wsBaseUrl}/v1/interviews/${encodeURIComponent(sessionId)}/ws`,
  );
  const [tenantId, setTenantId] = useState("tenant-default");
  const [statusNote, setStatusNote] = useState<string | null>(null);
  const [showAdvancedConn, setShowAdvancedConn] = useState(false);
  const syncedDemoRef = useRef(false);

  useEffect(() => {
    if (demoState.isBackendOnline && !syncedDemoRef.current) {
      syncedDemoRef.current = true;
      const jwt = demoState.manifest.candidate_jwt || demoState.manifest.recruiter_jwt;
      if (jwt.trim().length > 0) {
        setTicketToken(jwt);
      }
      setTenantId(demoState.manifest.tenant_id);
      setWsUrl(`${demoState.wsBaseUrl}/v1/interviews/${encodeURIComponent(sessionId)}/ws`);
    }
  }, [
    demoState.isBackendOnline,
    demoState.manifest.candidate_jwt,
    demoState.manifest.recruiter_jwt,
    demoState.manifest.tenant_id,
    demoState.wsBaseUrl,
    sessionId,
  ]);

  const canConnect = snapshot.state === "IDLE" || snapshot.state === "TERMINATED_ERROR";
  const canBargeIn = snapshot.state === "INTERVIEWER_SPEAKING";
  const canMarkReady = snapshot.state === "READY" || snapshot.state === "INTERVIEWER_SPEAKING";
  const canSubmitTurn =
    snapshot.state === "CANDIDATE_LISTENING" ||
    snapshot.state === "CANDIDATE_SPEAKING" ||
    snapshot.state === "BARGE_IN_INTERRUPTING" ||
    snapshot.state === "READY";
  const isTerminal = snapshot.state === "COMPLETED" || snapshot.state === "TERMINATED_ERROR";

  const wordCount = snapshot.draftSupplementalText.trim()
    ? snapshot.draftSupplementalText.trim().split(/\s+/).length
    : 0;
  const charCount = snapshot.draftSupplementalText.length;

  const displayedQuestion =
    snapshot.activeQuestion.length > 0
      ? snapshot.activeQuestion
      : demoState.manifest.active_session_question;

  const handleStart = async (e?: React.FormEvent): Promise<void> => {
    if (e) {
      e.preventDefault();
    }
    setStatusNote(null);
    try {
      await startSession({
        sessionId,
        tenantId,
        wsUrl,
        ticketToken,
        accommodatedTextMode: snapshot.accommodatedTextMode,
      });
    } catch (err) {
      setStatusNote(err instanceof Error ? err.message : "Failed to initialize live session.");
    }
  };

  const handleBargeIn = async (): Promise<void> => {
    setStatusNote(null);
    const interruptedMs = await triggerBargeIn();
    setStatusNote(
      `Barge-in transmitted at playback offset ${interruptedMs} ms; audio buffer flushed.`,
    );
  };

  const handleSubmitTurn = async (): Promise<void> => {
    setStatusNote(null);
    try {
      await submitUtterance();
    } catch (err) {
      setStatusNote(err instanceof Error ? err.message : "Unable to submit candidate turn.");
    }
  };

  const handleTerminate = async (): Promise<void> => {
    setStatusNote(null);
    await terminateSession("CANDIDATE_USER_REQUEST");
  };

  const handleComposerKeyDown = (ev: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter" && canSubmitTurn && !isTerminal) {
      ev.preventDefault();
      void handleSubmitTurn();
    }
  };

  const insertSampleArchitectureAnswer = (): void => {
    updateSupplementalText(
      "We combine bounded asyncio.Semaphore backpressure with PostgreSQL transactional outbox " +
        "writes and monotonic fencing tokens on lease acquisition, keeping p99 tail latency below " +
        "38ms while preventing split-brain duplicate processing.",
      "TYPED",
    );
  };

  const badgeClass = STATE_BADGE_CLASS[snapshot.state] ?? "badge badge-neutral";

  return (
    <div className="space-y-6">
      {/* Header Control Bar */}
      <header className="panel card-header-row">
        <div>
          <p className="eyebrow">Candidate Live Adaptive Interview Portal (ADR-010)</p>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="page-title">Session {formatShortUuid(sessionId)}</h1>
            <span className={badgeClass} data-testid="fsm-state-badge">
              {snapshot.state}
            </span>
          </div>
        </div>

        <div className="meta-strip">
          <span className="meta-pill">Turn #{snapshot.turnNumber}</span>
          <span className="meta-pill">Difficulty L{snapshot.difficulty}</span>
          <span className="meta-pill">Remaining {formatRemainingTime(snapshot.remainingSeconds)}</span>
          <span className="meta-pill">Jitter {jitterCushionMs} ms</span>
        </div>
      </header>

      {/* Primary Action Toolbar + Collapsible Connection Form */}
      <section className="panel" aria-label="Session Controls">
        <div className="section-row">
          <div className="button-row">
            <button
              type="button"
              className="btn btn-primary"
              disabled={!canConnect}
              onClick={() => void handleStart()}
            >
              Connect Interview Socket
            </button>
            <button
              type="button"
              className="btn btn-warning"
              disabled={!canBargeIn}
              onClick={() => void handleBargeIn()}
            >
              Interrupt &amp; Answer (Barge-In)
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!canMarkReady}
              onClick={markTurnInputReady}
            >
              Start Listening / Ready
            </button>
            <button
              type="button"
              className="btn btn-danger"
              disabled={snapshot.state === "IDLE" || isTerminal}
              onClick={() => void handleTerminate()}
            >
              End Session
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setShowAdvancedConn((v) => !v)}
            >
              {showAdvancedConn ? "Hide Socket Config" : "Socket Config"}
            </button>
          </div>

          <label className="toggle-control">
            <input
              type="checkbox"
              checked={snapshot.accommodatedTextMode}
              onChange={(ev) => setAccommodatedTextMode(ev.target.checked)}
            />
            <span>Accommodated Text Mode (Exempt from Cadence Telemetry)</span>
          </label>
        </div>

        {showAdvancedConn && (
          <form
            className="mt-4 grid grid-cols-1 gap-3 border-t border-slate-800 pt-4 md:grid-cols-3"
            onSubmit={(e) => void handleStart(e)}
          >
            <label className="field-group">
              <span>WebSocket Endpoint</span>
              <input
                type="text"
                value={wsUrl}
                onChange={(e) => setWsUrl(e.target.value)}
                disabled={!canConnect}
              />
            </label>
            <label className="field-group">
              <span>Tenant ID</span>
              <input
                type="text"
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                disabled={!canConnect}
              />
            </label>
            <label className="field-group">
              <span>Single-Use Session Ticket</span>
              <input
                type="password"
                value={ticketToken}
                onChange={(e) => setTicketToken(e.target.value)}
                disabled={!canConnect}
              />
            </label>
          </form>
        )}

        {snapshot.thinkingStage !== null && (
          <p className="status-banner info" role="status">
            Interviewer cognitive stage: <strong>{snapshot.thinkingStage}</strong>
          </p>
        )}
        {statusNote !== null && (
          <p className="status-banner info" role="status">
            {statusNote}
          </p>
        )}
        {snapshot.errorDetail !== null && (
          <p className="status-banner danger" role="alert">
            <strong>{snapshot.errorCode ?? "SESSION_ERROR"}:</strong> {snapshot.errorDetail}
          </p>
        )}
      </section>

      {/* Split-Pane Main Workspace (2/3 Conversation & Composer, 1/3 Telemetry Sidebar) */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Left Main Pane */}
        <div className="space-y-6 lg:col-span-2">
          {/* Active Question Card */}
          <section className="panel" aria-label="Active Question & Transcript">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="eyebrow">Active Adaptive Prompt</p>
                <h2 className="text-base font-semibold text-slate-100">
                  Interviewer Question &amp; Live Captions
                </h2>
              </div>
              <div className="badge-row">
                <span className="badge badge-info">Target L{snapshot.difficulty}</span>
                <span className="badge badge-accent">Turn #{snapshot.turnNumber}</span>
              </div>
            </div>

            <div className="question-box">
              {displayedQuestion.length > 0 ? (
                <p className="text-sm leading-relaxed text-slate-100">{displayedQuestion}</p>
              ) : (
                <p className="muted">
                  Waiting for session handshake and opening competency prompt...
                </p>
              )}
              {snapshot.state === "IDLE" && displayedQuestion.length > 0 && (
                <p className="mt-2 text-[11px] font-medium text-sky-400">
                  Preview loaded from Turn 1 SQL checkpoint — click &ldquo;Connect Interview
                  Socket&rdquo; above to start live interactive streaming.
                </p>
              )}
            </div>

            {snapshot.partialTranscript.length > 0 && (
              <div className="caption-box" aria-live="polite">
                <span className="eyebrow">Live Partial STT Transcript</span>
                <p className="text-sm text-slate-200">{snapshot.partialTranscript}</p>
              </div>
            )}

            <h3 className="subsection-title">
              Conversation Transcript Timeline ({snapshot.transcriptLedger.length})
            </h3>
            {snapshot.transcriptLedger.length === 0 ? (
              <p className="muted">No completed turns recorded in this browser session yet.</p>
            ) : (
              <ul className="ledger-list">
                {snapshot.transcriptLedger.map((entry, idx) => (
                  <li
                    key={`${entry.role}-${entry.turnNumber}-${idx}`}
                    className={`ledger-item ${
                      entry.role === "INTERVIEWER"
                        ? "border-l-4 border-l-sky-500"
                        : "border-l-4 border-l-emerald-500"
                    }`}
                  >
                    <div className="ledger-meta">
                      <span
                        className={`badge ${
                          entry.role === "INTERVIEWER" ? "badge-info" : "badge-success"
                        }`}
                      >
                        {entry.role}
                      </span>
                      <span className="badge badge-neutral">Turn #{entry.turnNumber}</span>
                      <span className="badge badge-neutral">L{entry.difficulty}</span>
                      {entry.truncatedAtMs !== null && (
                        <span className="badge badge-warning">
                          Interrupted @ {entry.truncatedAtMs} ms
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-slate-200">{entry.text}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Rich Technical Response Composer */}
          <section className="panel" aria-label="Candidate Response Composer">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="eyebrow">Technical Turn Input</p>
                <h2 className="text-base font-semibold text-slate-100">
                  Response Composer &amp; Architecture Scratchpad
                </h2>
              </div>
              <div className="button-row">
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={!canSubmitTurn || isTerminal}
                  onClick={insertSampleArchitectureAnswer}
                >
                  Insert Architecture Sample
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={!canSubmitTurn || isTerminal}
                  onClick={() =>
                    updateSupplementalText(
                      snapshot.draftSupplementalText || "Pasted external block...",
                      "PASTED",
                    )
                  }
                >
                  Paste Burst Test
                </button>
              </div>
            </div>

            <textarea
              className="code-textarea"
              rows={8}
              value={snapshot.draftSupplementalText}
              disabled={!canSubmitTurn || isTerminal}
              onKeyDown={handleComposerKeyDown}
              onChange={(ev) => updateSupplementalText(ev.target.value, "TYPED")}
              onPaste={() => updateSupplementalText(snapshot.draftSupplementalText, "PASTED")}
              placeholder="Explain architectural mechanisms, backpressure tradeoffs, p99 latency bounds, or SQL/Python snippets..."
            />

            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="muted font-mono text-xs">
                {wordCount} words · {charCount} chars · Shortcut: Ctrl/Cmd+Enter
              </span>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!canSubmitTurn || isTerminal}
                onClick={() => void handleSubmitTurn()}
              >
                Submit Turn Response
              </button>
            </div>
          </section>
        </div>

        {/* Right Sidebar: 13-State FSM, Audio/VAD & Integrity Telemetry */}
        <aside className="space-y-6">
          <div className="panel">
            <p className="eyebrow">Plane 1 State Machine</p>
            <h2 className="section-title">13-State FSM Live Inspector</h2>
            <div className="space-y-3 text-xs">
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
                <span className="text-slate-400">Active State</span>
                <span className={badgeClass}>{snapshot.state}</span>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
                <span className="text-slate-400">Tokens Consumed</span>
                <span className="font-mono font-semibold text-slate-200">
                  {snapshot.tokensConsumed}
                </span>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
                <span className="text-slate-400">Turn Window</span>
                <span className="font-mono font-semibold text-sky-300">
                  Turn #{snapshot.turnNumber} · L{snapshot.difficulty}
                </span>
              </div>
            </div>
          </div>

          <div className="panel">
            <p className="eyebrow">Web Audio Pipeline</p>
            <h2 className="section-title">VAD &amp; Ring-Buffer Telemetry</h2>
            <div className="space-y-3 text-xs">
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
                <span className="text-slate-400">Jitter Cushion</span>
                <span className="font-mono font-semibold text-emerald-300">
                  {jitterCushionMs} ms
                </span>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
                <span className="text-slate-400">Audio Chunks (Turn)</span>
                <span className="font-mono font-semibold text-slate-200">
                  {snapshot.audioChunksSentInTurn}
                </span>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
                <span className="text-slate-400">Last Barge-In Offset</span>
                <span className="font-mono font-semibold text-amber-300">
                  {snapshot.lastInterruptedPlaybackMs !== null
                    ? `${snapshot.lastInterruptedPlaybackMs} ms`
                    : "None"}
                </span>
              </div>
            </div>
          </div>

          <div className="panel">
            <p className="eyebrow">Anti-Cheat &amp; Fairness</p>
            <h2 className="section-title">Client Integrity Telemetry</h2>
            <div className="mb-3 flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2 text-xs">
              <span className="text-slate-400">Cadence Monitoring</span>
              <span
                className={`badge ${
                  snapshot.accommodatedTextMode ? "badge-info" : "badge-success"
                }`}
              >
                {snapshot.accommodatedTextMode ? "ACCOMMODATED EXEMPT" : "NOMINAL ACTIVE"}
              </span>
            </div>

            {snapshot.integrityWarnings.length === 0 ? (
              <p className="muted">
                No telemetry anomalies detected. Focus loss and paste burst signals nominal.
              </p>
            ) : (
              <ul className="warning-list">
                {snapshot.integrityWarnings.map((warn, idx) => (
                  <li
                    key={`${warn.reasonCode}-${warn.turnNumber}-${idx}`}
                    className="warning-item"
                  >
                    <strong>[{warn.severity}]</strong> Turn #{warn.turnNumber}: {warn.reasonCode}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
