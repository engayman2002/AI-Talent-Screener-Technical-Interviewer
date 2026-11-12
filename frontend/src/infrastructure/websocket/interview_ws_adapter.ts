/**
 * Reconnecting Full-Duplex WebSocket Client Adapter (ADR-007).
 * Enforces heartbeat keepalive pings, exponential backoff with jitter recovery,
 * monotonic sequence counter tracking, and automatic session.join re-sync.
 */

import type {
  ClientWsFrame,
  InterviewWebSocketPort,
  ServerWsFrameEnvelope,
  WsConnectionStatus,
  WsConnectOptions,
} from "../../application/ports/outbound";

export interface MinimalWebSocketLike {
  readonly readyState: number;
  onopen: (() => void) | null;
  onmessage: ((event: { readonly data: unknown }) => void) | null;
  onclose: ((event: { readonly code: number; readonly reason: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export interface WsScheduler {
  setTimeout(fn: () => void, ms: number): ReturnType<typeof setTimeout>;
  clearTimeout(id: ReturnType<typeof setTimeout>): void;
  setInterval(fn: () => void, ms: number): ReturnType<typeof setInterval>;
  clearInterval(id: ReturnType<typeof setInterval>): void;
  nowMs(): number;
  random(): number;
}

const DEFAULT_SCHEDULER: WsScheduler = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (id) => globalThis.clearTimeout(id),
  setInterval: (fn, ms) => globalThis.setInterval(fn, ms),
  clearInterval: (id) => globalThis.clearInterval(id),
  nowMs: () => Date.now(),
  random: () => Math.random(),
};

export interface ReconnectingWsOptions {
  readonly socketFactory?: (url: string) => MinimalWebSocketLike;
  readonly scheduler?: WsScheduler;
  readonly heartbeatIntervalMs?: number;
  readonly graceTimeoutMs?: number;
  readonly baseBackoffMs?: number;
  readonly maxBackoffMs?: number;
}

export class ReconnectingInterviewWebSocketAdapter implements InterviewWebSocketPort {
  private readonly socketFactory: (url: string) => MinimalWebSocketLike;
  private readonly scheduler: WsScheduler;
  private readonly heartbeatIntervalMs: number;
  private readonly graceTimeoutMs: number;
  private readonly baseBackoffMs: number;
  private readonly maxBackoffMs: number;

  private socket: MinimalWebSocketLike | null = null;
  private connectOptions: WsConnectOptions | null = null;
  private intentionalClose = false;
  private status: WsConnectionStatus = "DISCONNECTED";
  private lastServerSeq = 0;
  private reconnectAttempt = 0;
  private disconnectStartedAtMs: number | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;

  private readonly frameHandlers = new Set<(frame: ServerWsFrameEnvelope) => void>();
  private readonly statusHandlers = new Set<(status: WsConnectionStatus) => void>();

  public constructor(opts: ReconnectingWsOptions = {}) {
    this.socketFactory =
      opts.socketFactory ??
      ((url: string) => new globalThis.WebSocket(url) as unknown as MinimalWebSocketLike);
    this.scheduler = opts.scheduler ?? DEFAULT_SCHEDULER;
    this.heartbeatIntervalMs = opts.heartbeatIntervalMs ?? 10_000;
    this.graceTimeoutMs = opts.graceTimeoutMs ?? 180_000;
    this.baseBackoffMs = opts.baseBackoffMs ?? 500;
    this.maxBackoffMs = opts.maxBackoffMs ?? 8_000;
  }

  public getLastServerSeq(): number {
    return this.lastServerSeq;
  }

  public onFrame(handler: (frame: ServerWsFrameEnvelope) => void): () => void {
    this.frameHandlers.add(handler);
    return () => {
      this.frameHandlers.delete(handler);
    };
  }

  public onConnectionStatusChange(handler: (status: WsConnectionStatus) => void): () => void {
    this.statusHandlers.add(handler);
    return () => {
      this.statusHandlers.delete(handler);
    };
  }

  public async connect(options: WsConnectOptions): Promise<void> {
    this.intentionalClose = false;
    this.connectOptions = options;
    if (options.lastServerSeq !== undefined) {
      this.lastServerSeq = Math.max(this.lastServerSeq, options.lastServerSeq);
    }
    this.setStatus("CONNECTING");
    await this.openSocket(options, false);
  }

  public async disconnect(): Promise<void> {
    this.intentionalClose = true;
    this.clearTimers();
    if (this.socket !== null) {
      this.socket.close(1000, "Client disconnected");
      this.socket = null;
    }
    this.setStatus("DISCONNECTED");
  }

  public async sendFrame(frame: ClientWsFrame): Promise<void> {
    if (this.socket === null || this.socket.readyState !== 1) {
      throw new Error(`Cannot send WebSocket frame '${frame.event_type}': socket not open.`);
    }
    this.socket.send(JSON.stringify(frame));
  }

  private async openSocket(options: WsConnectOptions, isReconnect: boolean): Promise<void> {
    const sep = options.url.includes("?") ? "&" : "?";
    const fullUrl = `${options.url}${sep}ticket=${encodeURIComponent(options.authToken)}`;

    await new Promise<void>((resolve, reject) => {
      const ws = this.socketFactory(fullUrl);
      this.socket = ws;
      let settled = false;

      ws.onopen = () => {
        this.reconnectAttempt = 0;
        this.disconnectStartedAtMs = null;
        this.setStatus("CONNECTED");
        this.startHeartbeat();
        if (isReconnect) {
          ws.send(
            JSON.stringify({
              event_type: "session.join",
              payload: {
                session_id: options.sessionId,
                last_received_seq: this.lastServerSeq,
              },
            }),
          );
        }
        if (!settled) {
          settled = true;
          resolve();
        }
      };

      ws.onmessage = (evt) => {
        if (typeof evt.data !== "string") {
          return;
        }
        try {
          const parsed = JSON.parse(evt.data) as ServerWsFrameEnvelope;
          if (typeof parsed.seq === "number" && parsed.seq > this.lastServerSeq) {
            this.lastServerSeq = parsed.seq;
          }
          for (const handler of this.frameHandlers) {
            handler(parsed);
          }
        } catch {
          // Ignore unparseable transport noise cleanly
        }
      };

      ws.onerror = () => {
        if (!settled) {
          settled = true;
          reject(new Error("WebSocket connection failed."));
        }
      };

      ws.onclose = () => {
        this.stopHeartbeat();
        if (this.intentionalClose) {
          return;
        }
        this.scheduleReconnect();
      };
    });
  }

  private scheduleReconnect(): void {
    const now = this.scheduler.nowMs();
    if (this.disconnectStartedAtMs === null) {
      this.disconnectStartedAtMs = now;
    }
    if (now - this.disconnectStartedAtMs >= this.graceTimeoutMs || this.connectOptions === null) {
      this.setStatus("DISCONNECTED");
      return;
    }

    this.setStatus("RECONNECTING");
    const cap = Math.min(this.maxBackoffMs, this.baseBackoffMs * Math.pow(2, this.reconnectAttempt));
    const jittered = Math.max(50, Math.round(cap * (0.5 + 0.5 * this.scheduler.random())));
    this.reconnectAttempt += 1;

    const opts = this.connectOptions;
    this.reconnectTimer = this.scheduler.setTimeout(() => {
      if (!this.intentionalClose) {
        void this.openSocket(opts, true).catch(() => {
          // Next close/error triggers backoff continuation until grace expiry
        });
      }
    }, jittered);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = this.scheduler.setInterval(() => {
      if (this.socket !== null && this.socket.readyState === 1) {
        this.socket.send(
          JSON.stringify({
            event_type: "ping",
            payload: {
              client_timestamp: new Date(this.scheduler.nowMs()).toISOString(),
              last_received_seq: this.lastServerSeq,
            },
          }),
        );
      }
    }, this.heartbeatIntervalMs);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      this.scheduler.clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private clearTimers(): void {
    this.stopHeartbeat();
    if (this.reconnectTimer !== null) {
      this.scheduler.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private setStatus(next: WsConnectionStatus): void {
    if (this.status === next) {
      return;
    }
    this.status = next;
    for (const handler of this.statusHandlers) {
      handler(next);
    }
  }
}
