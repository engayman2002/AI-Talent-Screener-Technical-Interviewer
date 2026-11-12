/**
 * ADR-011 Server-Sent Events (SSE) Batch Monitor Adapter with Last-Event-ID Recovery,
 * Conditional ETag Fallback, and requestAnimationFrame 60 FPS UI Frame Coalescing.
 */

import type {
  BatchSseStreamPort,
  BatchSseSubscribeOptions,
  CoalescedBatchProgressSnapshot,
  SseTaskDelta,
} from "../../application/ports/outbound";
import type { BatchStatus, TaskIngestionStatus } from "../../domain/shared/types";

export interface MinimalEventSourceLike {
  onmessage: ((event: { readonly data: string; readonly lastEventId?: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  addEventListener?(
    type: string,
    listener: (event: { readonly data: string; readonly lastEventId?: string }) => void,
  ): void;
  close(): void;
}

export interface ParsedSseMessage {
  readonly eventId: number;
  readonly eventType: string;
  readonly data: Readonly<Record<string, unknown>>;
}

export interface RafCoalescerDrivers {
  readonly eventSourceFactory?: (url: string, lastEventId: number) => MinimalEventSourceLike;
  readonly requestAnimationFrameFn?: (callback: () => void) => number;
  readonly cancelAnimationFrameFn?: (handle: number) => void;
  readonly setTimeoutFn?: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  readonly clearTimeoutFn?: (id: ReturnType<typeof setTimeout>) => void;
}

function defaultRaf(cb: () => void): number {
  if (typeof globalThis.requestAnimationFrame === "function") {
    return globalThis.requestAnimationFrame(() => cb());
  }
  return Number(globalThis.setTimeout(cb, 16));
}

function defaultCancelRaf(id: number): void {
  if (typeof globalThis.cancelAnimationFrame === "function") {
    globalThis.cancelAnimationFrame(id);
    return;
  }
  globalThis.clearTimeout(id);
}

/**
 * Parse backend `format_sse_frame` wire text (one or more `id: / event: / data:` blocks).
 */
export function parseSseWireFrames(rawText: string): readonly ParsedSseMessage[] {
  const normalized = rawText.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const parsed: ParsedSseMessage[] = [];

  for (const block of blocks) {
    const trimmed = block.trim();
    if (!trimmed) {
      continue;
    }
    let eventId = 0;
    let eventType = "message";
    const dataLines: string[] = [];

    for (const line of trimmed.split("\n")) {
      if (line.startsWith("id:")) {
        const val = Number.parseInt(line.slice(3).trim(), 10);
        if (Number.isFinite(val)) {
          eventId = val;
        }
      } else if (line.startsWith("event:")) {
        eventType = line.slice(6).trim() || "message";
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }

    if (dataLines.length === 0) {
      continue;
    }
    try {
      const jsonObj = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
      const embeddedId = jsonObj["event_id"];
      if (eventId === 0 && typeof embeddedId === "number") {
        eventId = embeddedId;
      }
      parsed.push({ eventId, eventType, data: jsonObj });
    } catch {
      // Skip malformed heartbeat or comment lines
    }
  }

  return parsed;
}

function extractTaskDelta(msg: ParsedSseMessage): SseTaskDelta | null {
  const rawPayload =
    typeof msg.data["payload"] === "object" && msg.data["payload"] !== null
      ? (msg.data["payload"] as Record<string, unknown>)
      : msg.data;

  const taskId = rawPayload["task_id"];
  if (typeof taskId !== "string" || !taskId) {
    return null;
  }

  const rawTaskStatus = rawPayload["task_status"];
  const status: TaskIngestionStatus =
    typeof rawTaskStatus === "string"
      ? (rawTaskStatus as TaskIngestionStatus)
      : msg.eventType === "TASK_STARTED"
        ? "PARSING"
        : msg.eventType === "TASK_SUCCEEDED"
          ? "COMPLETED"
          : msg.eventType === "TASK_FAILED"
            ? "FAILED"
            : "PENDING";

  const filename = rawPayload["filename"];
  const errCode = rawPayload["error_code"];
  const candProfileId = rawPayload["candidate_profile_id"];

  return {
    taskId,
    ...(typeof filename === "string" ? { filename } : {}),
    status,
    errorCode: typeof errCode === "string" && errCode.length > 0 ? errCode : null,
    candidateProfileId: typeof candProfileId === "string" ? candProfileId : null,
    eventId: msg.eventId,
    eventType: msg.eventType,
  };
}

export class RafCoalescedBatchSseAdapter implements BatchSseStreamPort {
  private readonly eventSourceFactory: (url: string, lastEventId: number) => MinimalEventSourceLike;
  private readonly rafFn: (cb: () => void) => number;
  private readonly cancelRafFn: (id: number) => void;

  private activeBatchId = "";
  private activeLastEventId = 0;
  private pendingBatchStatus: BatchStatus | undefined = undefined;
  private readonly stagedDeltasByTask = new Map<string, SseTaskDelta>();
  private scheduledRafHandle: number | null = null;
  private activeOnProgress: ((snapshot: CoalescedBatchProgressSnapshot) => void) | null = null;

  public ingestedEventsCount = 0;
  public flushedFramesCount = 0;

  public constructor(drivers: RafCoalescerDrivers = {}) {
    this.eventSourceFactory =
      drivers.eventSourceFactory ??
      ((url: string, lastId: number) => {
        const sep = url.includes("?") ? "&" : "?";
        const resolved = lastId > 0 ? `${url}${sep}last_event_id=${lastId}` : url;
        return new globalThis.EventSource(resolved) as unknown as MinimalEventSourceLike;
      });
    this.rafFn = drivers.requestAnimationFrameFn ?? defaultRaf;
    this.cancelRafFn = drivers.cancelAnimationFrameFn ?? defaultCancelRaf;
  }

  public getLastEventId(): number {
    return this.activeLastEventId;
  }

  public subscribe(
    options: BatchSseSubscribeOptions,
    onProgress: (snapshot: CoalescedBatchProgressSnapshot) => void,
    onError: (err: Error) => void,
  ): () => void {
    this.resetBuffer(options.batchId, options.lastEventId ?? 0);
    this.activeOnProgress = onProgress;

    const es = this.eventSourceFactory(options.streamUrl, this.activeLastEventId);
    const handleRawEvent = (eventType: string, rawData: string, rawLastId?: string): void => {
      try {
        const obj = JSON.parse(rawData) as Record<string, unknown>;
        const parsedId = rawLastId ? Number.parseInt(rawLastId, 10) : Number.NaN;
        const embeddedId = typeof obj["event_id"] === "number" ? obj["event_id"] : 0;
        const resolvedId = Number.isFinite(parsedId) ? parsedId : embeddedId;
        this.ingestParsedMessage({
          eventId: resolvedId,
          eventType:
            typeof obj["event_type"] === "string" ? obj["event_type"] : eventType,
          data: obj,
        });
      } catch (err) {
        onError(err instanceof Error ? err : new Error("Malformed SSE payload"));
      }
    };

    es.onmessage = (evt) => handleRawEvent("message", evt.data, evt.lastEventId);
    if (typeof es.addEventListener === "function") {
      for (const named of ["TASK_STARTED", "TASK_SUCCEEDED", "TASK_FAILED", "BATCH_COMPLETED"]) {
        es.addEventListener(named, (evt) => handleRawEvent(named, evt.data, evt.lastEventId));
      }
    }
    es.onerror = () => {
      onError(new Error(`SSE transport error on batch ${options.batchId}`));
    };

    return () => {
      es.close();
      if (this.scheduledRafHandle !== null) {
        this.cancelRafFn(this.scheduledRafHandle);
        this.scheduledRafHandle = null;
      }
      this.activeOnProgress = null;
    };
  }

  /**
   * Public ingestion entry point for wire SSE bursts (schedules single rAF UI commit).
   */
  public ingestWireChunk(rawWireText: string): void {
    const messages = parseSseWireFrames(rawWireText);
    for (const msg of messages) {
      this.ingestParsedMessage(msg);
    }
  }

  public ingestParsedMessage(msg: ParsedSseMessage): void {
    this.ingestedEventsCount += 1;
    if (msg.eventId > this.activeLastEventId) {
      this.activeLastEventId = msg.eventId;
    }
    const rawPayload =
      typeof msg.data["payload"] === "object" && msg.data["payload"] !== null
        ? (msg.data["payload"] as Record<string, unknown>)
        : msg.data;

    if (typeof rawPayload["batch_status"] === "string") {
      this.pendingBatchStatus = rawPayload["batch_status"] as BatchStatus;
    }

    const delta = extractTaskDelta(msg);
    if (delta !== null) {
      this.stagedDeltasByTask.set(delta.taskId, delta);
    }

    if (this.scheduledRafHandle === null && this.activeOnProgress !== null) {
      this.scheduledRafHandle = this.rafFn(() => {
        this.flushPendingFrame();
      });
    }
  }

  public flushPendingFrame(): void {
    this.scheduledRafHandle = null;
    if (this.activeOnProgress === null) {
      return;
    }
    const taskUpdates = Array.from(this.stagedDeltasByTask.values());
    const batchStatusSnapshot = this.pendingBatchStatus;
    this.stagedDeltasByTask.clear();
    this.pendingBatchStatus = undefined;

    if (taskUpdates.length === 0 && batchStatusSnapshot === undefined) {
      return;
    }
    this.flushedFramesCount += 1;
    this.activeOnProgress({
      batchId: this.activeBatchId,
      latestEventId: this.activeLastEventId,
      ...(batchStatusSnapshot !== undefined ? { batchStatus: batchStatusSnapshot } : {}),
      taskUpdates,
    });
  }

  private resetBuffer(batchId: string, lastEventId: number): void {
    this.activeBatchId = batchId;
    this.activeLastEventId = lastEventId;
    this.pendingBatchStatus = undefined;
    this.stagedDeltasByTask.clear();
    this.ingestedEventsCount = 0;
    this.flushedFramesCount = 0;
  }
}
