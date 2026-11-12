"use client";

import React, { useState } from "react";
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

  const [ticketToken, setTicketToken] = useState("candidate-demo-ticket");
  const [wsUrl, setWsUrl] = useState(
    `wss://localhost:8000/v1/ws/interviews/${sessionId}`,
  );
  const [tenantId, setTenantId] = useState("tenant-default");
  const [statusNote, setStatusNote] = useState<string | null>(null);

  const canConnect =
    snapshot.state === "IDLE" || snapshot.state === "TERMINATED_ERROR";
  const canBargeIn = snapshot.state === "INTERVIEWER_SPEAKING";
  const canMarkReady =
    snapshot.state === "READY" || snapshot.state === "INTERVIEWER_SPEAKING";
  const canSubmitTurn =
    snapshot.state === "CANDIDATE_LISTENING" ||
    snapshot.state === "CANDIDATE_SPEAKING" ||
    snapshot.state === "BARGE_IN_INTERRUPTING" ||
    snapshot.state === "READY";
  const isTerminal =
    snapshot.state === "COMPLETED" || snapshot.state === "TERMINATED_ERROR";

  const handleStart = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
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
      setStatusNote(
        err instanceof Error ? err.message : "Failed to initialize live session.",
      );
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
      setStatusNote(
        err instanceof Error ? err.message : "Unable to submit candidate turn.",
      );
    }
  };

  const handleTerminate = async (): Promise<void> => {
    setStatusNote(null);
    await terminateSession("CANDIDATE_USER_REQUEST");
  };

  const badgeClass = STATE_BADGE_CLASS[snapshot.state] ?? "badge badge-neutral";

  return (
    <div className="panel-stack">
      <header className="panel card-header-row">
        <div>
          <p className="eyebrow">Candidate Live Adaptive Interview Portal (ADR-010)</p>
          <h1 className="page-title">Session {sessionId}</h1>
        </div>
        <div className="meta-strip">
          <span className={badgeClass} data-testid="fsm-state-badge">
            {snapshot.state}
          </span>
          <span className="meta-pill">Turn #{snapshot.turnNumber}</span>
          <span className="meta-pill">Difficulty L{snapshot.difficulty}</span>
          <span className="meta-pill">
            Remaining {formatRemainingTime(snapshot.remainingSeconds)}
          </span>
          <span className="meta-pill">Jitter {jitterCushionMs} ms</span>
        </div>
      </header>

      <section className="panel" aria-label="Session Controls">
        <div className="section-row">
          <h2 className="section-title">Session Connection & Accessibility</h2>
          <label className="toggle-control">
            <input
              type="checkbox"
              checked={snapshot.accommodatedTextMode}
              onChange={(ev) => setAccommodatedTextMode(ev.target.checked)}
            />
            <span>Accommodated Text Mode (Exempt from Cadence Telemetry)</span>
          </label>
        </div>

        <form className="form-grid" onSubmit={(e) => void handleStart(e)}>
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
          <div className="button-row">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={!canConnect}
            >
              Connect Interview Socket
            </button>
            <button
              type="button"
              className="btn btn-warning"
              disabled={!canBargeIn}
              onClick={() => void handleBargeIn()}
            >
              Interrupt & Answer (Barge-In)
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
          </div>
        </form>

        {snapshot.thinkingStage !== null && (
          <p className="status-banner info" role="status">
            Interviewer cognitive stage: <strong>{snapshot.thinkingStage}</strong>
          </p>
        )}
        {snapshot.lastInterruptedPlaybackMs !== null && (
          <p className="status-banner neutral" role="status">
            Last barge-in offset recorded at{" "}
            <strong>{snapshot.lastInterruptedPlaybackMs} ms</strong>.
          </p>
        )}
        {statusNote !== null && (
          <p className="status-banner info" role="status">
            {statusNote}
          </p>
        )}
        {snapshot.errorDetail !== null && (
          <p className="status-banner danger" role="alert">
            <strong>{snapshot.errorCode ?? "SESSION_ERROR"}:</strong>{" "}
            {snapshot.errorDetail}
          </p>
        )}
      </section>

      <div className="two-col-grid">
        <section className="panel" aria-label="Active Question & Transcript">
          <h2 className="section-title">Interviewer Prompt & Live Captions</h2>
          <div className="question-box">
            {snapshot.activeQuestion.length > 0 ? (
              <p>{snapshot.activeQuestion}</p>
            ) : (
              <p className="muted">
                Waiting for session handshake and opening competency prompt...
              </p>
            )}
          </div>

          {snapshot.partialTranscript.length > 0 && (
            <div className="caption-box" aria-live="polite">
              <span className="eyebrow">Live Partial STT Transcript</span>
              <p>{snapshot.partialTranscript}</p>
            </div>
          )}

          <h3 className="subsection-title">Turn Transcript Ledger</h3>
          {snapshot.transcriptLedger.length === 0 ? (
            <p className="muted">No completed turns recorded yet.</p>
          ) : (
            <ul className="ledger-list">
              {snapshot.transcriptLedger.map((entry, idx) => (
                <li
                  key={`${entry.role}-${entry.turnNumber}-${idx}`}
                  className="ledger-item"
                >
                  <div className="ledger-meta">
                    <strong>{entry.role}</strong> · Turn #{entry.turnNumber} · L
                    {entry.difficulty}
                    {entry.truncatedAtMs !== null
                      ? ` · Interrupted @ ${entry.truncatedAtMs} ms`
                      : ""}
                  </div>
                  <p>{entry.text}</p>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel" aria-label="Candidate Response Composer">
          <h2 className="section-title">Supplemental Text & Code Scratchpad</h2>
          <p className="muted">
            Type or paste architecture notes, complexity bounds, or code snippets to
            accompany your spoken response.
          </p>
          <textarea
            className="code-textarea"
            rows={10}
            value={snapshot.draftSupplementalText}
            disabled={!canSubmitTurn || isTerminal}
            onChange={(ev) => updateSupplementalText(ev.target.value, "TYPED")}
            onPaste={() =>
              updateSupplementalText(snapshot.draftSupplementalText, "PASTED")
            }
            placeholder="Write your technical rationale, SQL, or complexity analysis..."
          />
          <div className="section-row">
            <span className="muted">
              Audio chunks streamed this turn: {snapshot.audioChunksSentInTurn} | Tokens
              consumed: {snapshot.tokensConsumed}
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

          <h3 className="subsection-title">Integrity Telemetry Notices</h3>
          {snapshot.integrityWarnings.length === 0 ? (
            <p className="muted">
              Telemetry nominal. Non-intrusive cadence and focus monitoring active.
            </p>
          ) : (
            <ul className="warning-list">
              {snapshot.integrityWarnings.map((warn, idx) => (
                <li
                  key={`${warn.reasonCode}-${warn.turnNumber}-${idx}`}
                  className="warning-item"
                >
                  <strong>[{warn.severity}]</strong> Turn #{warn.turnNumber}:{" "}
                  {warn.reasonCode}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
