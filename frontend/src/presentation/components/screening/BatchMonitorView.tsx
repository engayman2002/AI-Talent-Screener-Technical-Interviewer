"use client";

import React, { useEffect, useRef, useState } from "react";
import type { BatchFileManifestWire } from "../../../application/ports/outbound";
import { validateBatchManifest } from "../../../application/screening/batch_monitor_coordinator";
import { ValidationDomainError } from "../../../domain/shared/errors";
import { useDemoBootstrap } from "../../hooks/useDemoBootstrap";
import { useBatchMonitor } from "../../hooks/useBatchMonitor";

export interface BatchMonitorViewProps {
  readonly batchId: string;
}

interface PendingLocalFileEntry {
  readonly file: File;
  readonly filename: string;
  readonly sizeBytes: number;
  readonly contentType: string;
}

function inferContentType(file: File): string {
  if (file.type.length > 0) {
    return file.type;
  }
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".pdf")) {
    return "application/pdf";
  }
  if (lower.endsWith(".docx")) {
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  }
  if (lower.endsWith(".md")) {
    return "text/markdown";
  }
  return "text/plain";
}

async function computeSha256Hex(file: File): Promise<string> {
  const bytes = await file.arrayBuffer();
  if (typeof globalThis.crypto !== "undefined" && globalThis.crypto.subtle !== undefined) {
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }
  return `${file.name}:${file.size}:${file.lastModified}`
    .padEnd(64, "0")
    .slice(0, 64);
}

export function BatchMonitorView({
  batchId,
}: BatchMonitorViewProps): React.JSX.Element {
  const { state, initiateBatch, subscribeBatch, refreshSnapshot, stop } =
    useBatchMonitor();
  const { state: demoState, simulateLiveBatch } = useDemoBootstrap();

  const [requisitionId, setRequisitionId] = useState("req-staff-backend-01");
  const [batchLabel, setBatchLabel] = useState("Q4 Pipeline Bulk Screening");
  const [streamPath, setStreamPath] = useState(
    `${demoState.apiBaseUrl}/v1/screening-batches/${batchId}/events`,
  );
  const [selectedEntries, setSelectedEntries] = useState<
    readonly PendingLocalFileEntry[]
  >([]);
  const [localError, setLocalError] = useState<string | null>(null);
  const [simRunning, setSimRunning] = useState(false);
  const loadedSeedBatchRef = useRef(false);

  useEffect(() => {
    if (demoState.isBackendOnline && !loadedSeedBatchRef.current) {
      loadedSeedBatchRef.current = true;
      setRequisitionId(demoState.manifest.requisition_id);
      void refreshSnapshot(batchId);
    }
  }, [
    batchId,
    demoState.isBackendOnline,
    demoState.manifest.requisition_id,
    refreshSnapshot,
  ]);

  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);

  const handleSimulateBatch = async (): Promise<void> => {
    setLocalError(null);
    setSimRunning(true);
    try {
      const sim = await simulateLiveBatch();
      const fullStreamUrl = `${demoState.apiBaseUrl}${sim.sse_events_path}`;
      setStreamPath(fullStreamUrl);
      await refreshSnapshot(sim.batch_id);
      subscribeBatch(sim.batch_id, fullStreamUrl);
    } catch (err) {
      setLocalError(
        err instanceof Error ? err.message : "Failed to start live batch simulation.",
      );
    } finally {
      setSimRunning(false);
    }
  };

  const progressPercent =
    state.totalFiles > 0
      ? Math.round(
          ((state.completedCount + state.failedCount) / state.totalFiles) * 100,
        )
      : 0;

  const handleFileSelection = (ev: React.ChangeEvent<HTMLInputElement>): void => {
    setLocalError(null);
    const list = ev.target.files;
    if (list === null || list.length === 0) {
      setSelectedEntries([]);
      return;
    }
    const entries: PendingLocalFileEntry[] = [];
    for (let i = 0; i < list.length; i += 1) {
      const f = list.item(i);
      if (f !== null) {
        entries.push({
          file: f,
          filename: f.name,
          sizeBytes: f.size,
          contentType: inferContentType(f),
        });
      }
    }
    setSelectedEntries(entries);
  };

  const handleInitiate = async (ev: React.FormEvent): Promise<void> => {
    ev.preventDefault();
    setLocalError(null);
    try {
      const manifest: BatchFileManifestWire[] = await Promise.all(
        selectedEntries.map(async (entry) => ({
          filename: entry.filename,
          sha256_hex: await computeSha256Hex(entry.file),
          file_size_bytes: entry.sizeBytes,
          mime_type: entry.contentType,
        })),
      );
      validateBatchManifest(manifest);
      const idempotencyKey = `batch-${requisitionId}-${Date.now()}`;
      await initiateBatch({
        requisitionId,
        batchLabel,
        files: manifest,
        idempotencyKey,
      });
    } catch (err) {
      if (err instanceof ValidationDomainError) {
        setLocalError(`${err.code}: ${err.message}`);
      } else {
        setLocalError(
          err instanceof Error ? err.message : "Batch initiation failed.",
        );
      }
    }
  };

  return (
    <div className="panel-stack">
      <header className="panel card-header-row">
        <div>
          <p className="eyebrow">Recruiter 500-CV Bulk Ingestion & SSE Monitor (ADR-011)</p>
          <h1 className="page-title">
            Batch {state.batchId ?? batchId}
          </h1>
        </div>
        <div className="meta-strip">
          <span className="badge badge-info">{state.batchStatus}</span>
          <span className="meta-pill">
            Stream: {state.isStreaming ? "LIVE" : "IDLE"}
          </span>
          <span className="meta-pill">Last-Event-ID: {state.latestEventId}</span>
        </div>
      </header>

      <section className="panel" aria-label="Bulk CV Manifest Uploader">
        <h2 className="section-title">Initiate New Screening Batch (Up to 500 CVs)</h2>
        <form className="form-grid" onSubmit={(e) => void handleInitiate(e)}>
          <label className="field-group">
            <span>Requisition ID</span>
            <input
              type="text"
              value={requisitionId}
              onChange={(e) => setRequisitionId(e.target.value)}
            />
          </label>
          <label className="field-group">
            <span>Batch Label</span>
            <input
              type="text"
              value={batchLabel}
              onChange={(e) => setBatchLabel(e.target.value)}
            />
          </label>
          <label className="field-group">
            <span>Select CV Files (.pdf, .docx, .txt, .md — max 15 MB each)</span>
            <input
              type="file"
              multiple
              accept=".pdf,.docx,.txt,.md"
              onChange={handleFileSelection}
            />
          </label>
          <div className="button-row">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={selectedEntries.length === 0}
            >
              Initiate Batch ({selectedEntries.length} selected)
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!demoState.isBackendOnline || simRunning}
              onClick={() => void handleSimulateBatch()}
            >
              {simRunning ? "Simulating..." : "Simulate Live Batch (SSE)"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => subscribeBatch(state.batchId ?? batchId, streamPath)}
            >
              Subscribe SSE Stream
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => void refreshSnapshot(state.batchId ?? batchId)}
            >
              Poll Snapshot
            </button>
            <button type="button" className="btn btn-danger" onClick={stop}>
              Disconnect Stream
            </button>
          </div>
        </form>

        <label className="field-group compact-top">
          <span>SSE Stream Endpoint</span>
          <input
            type="text"
            value={streamPath}
            onChange={(e) => setStreamPath(e.target.value)}
          />
        </label>

        {localError !== null && (
          <p className="status-banner danger" role="alert">
            {localError}
          </p>
        )}
        {state.problemError !== null && (
          <div className="status-banner danger" role="alert">
            <strong>
              {state.problemError.error_code} (HTTP {state.problemError.status}):
            </strong>{" "}
            {state.problemError.detail}
          </div>
        )}
      </section>

      <section className="panel" aria-label="60 FPS Coalesced Progress Meter">
        <div className="section-row">
          <h2 className="section-title">Batch Progress</h2>
          <span className="meta-pill">
            {progressPercent}% ({state.completedCount + state.failedCount} /{" "}
            {state.totalFiles})
          </span>
        </div>
        <div
          className="progress-track"
          role="progressbar"
          aria-valuenow={progressPercent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="progress-fill"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
        <div className="kpi-grid">
          <div className="kpi-card">
            <span className="eyebrow">Total Files</span>
            <strong>{state.totalFiles}</strong>
          </div>
          <div className="kpi-card">
            <span className="eyebrow">Completed</span>
            <strong>{state.completedCount}</strong>
          </div>
          <div className="kpi-card">
            <span className="eyebrow">Failed / Quarantined</span>
            <strong>{state.failedCount}</strong>
          </div>
          <div className="kpi-card">
            <span className="eyebrow">Pending</span>
            <strong>{state.pendingCount}</strong>
          </div>
        </div>
      </section>

      <section className="panel" aria-label="Per-CV Ingestion Task Ledger">
        <h2 className="section-title">Ingestion Tasks ({state.tasks.length})</h2>
        {state.tasks.length === 0 ? (
          <p className="muted">
            No ingestion tasks loaded yet. Initiate a batch or connect to the SSE
            stream to monitor live CV parsing and indexing stages.
          </p>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Task ID</th>
                  <th>Filename</th>
                  <th>Stage Status</th>
                  <th>Candidate Profile</th>
                  <th>Error Code</th>
                </tr>
              </thead>
              <tbody>
                {state.tasks.map((task) => (
                  <tr key={task.taskId}>
                    <td>
                      <code>{task.taskId}</code>
                    </td>
                    <td>{task.filename}</td>
                    <td>
                      <span className="badge badge-neutral">{task.status}</span>
                    </td>
                    <td>{task.candidateProfileId ?? "—"}</td>
                    <td>{task.errorCode ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
