/**
 * Non-Intrusive DOM Focus, Paste-Burst & Keystroke Kinematic Telemetry Adapter.
 * Implements IntegrityTelemetryCollectorPort without storing raw PII clipboard or key strings.
 */

import type {
  IntegrityTelemetryCollectorPort,
  TelemetrySpikeAlert,
} from "../../application/ports/outbound";
import {
  buildTelemetrySnapshotPayload,
  type ComputedTurnTelemetry,
  type TelemetryWirePayload,
  type TextInsertionSample,
} from "../../domain/interview/telemetry_math";
import type { DifficultyLevel } from "../../domain/shared/types";

export interface TelemetryDomTarget {
  readonly hidden: boolean;
  addVisibilityListener(fn: (hidden: boolean) => void): () => void;
  addWindowBlurFocusListeners(onBlur: () => void, onFocus: () => void): () => void;
  nowMs(): number;
}

function createDefaultDomTarget(): TelemetryDomTarget {
  return {
    get hidden() {
      return typeof globalThis.document !== "undefined" ? Boolean(globalThis.document.hidden) : false;
    },
    addVisibilityListener(fn) {
      if (typeof globalThis.document === "undefined") {
        return () => undefined;
      }
      const handler = (): void => fn(Boolean(globalThis.document.hidden));
      globalThis.document.addEventListener("visibilitychange", handler);
      return () => globalThis.document.removeEventListener("visibilitychange", handler);
    },
    addWindowBlurFocusListeners(onBlur, onFocus) {
      if (typeof globalThis.window === "undefined") {
        return () => undefined;
      }
      globalThis.window.addEventListener("blur", onBlur);
      globalThis.window.addEventListener("focus", onFocus);
      return () => {
        globalThis.window.removeEventListener("blur", onBlur);
        globalThis.window.removeEventListener("focus", onFocus);
      };
    },
    nowMs: () => Date.now(),
  };
}

export class BrowserIntegrityTelemetryAdapter implements IntegrityTelemetryCollectorPort {
  private readonly dom: TelemetryDomTarget;
  private readonly domUnsubscribers: (() => void)[] = [];
  private readonly spikeListeners = new Set<(alert: TelemetrySpikeAlert) => void>();

  private attached = false;
  private turnNumber = 1;
  private difficulty: DifficultyLevel = 3;
  private accommodatedTextMode = false;
  private turnStartedAtMs = 0;
  private activeBlurStartedAtMs: number | null = null;
  private lastInputTimestampMs: number | null = null;
  private ttftMs = 0;
  private speechWpm = 0;
  private pauseEntropy = 0;
  private totalTypedChars = 0;

  private blurDurationsMs: number[] = [];
  private interKeyIntervalsMs: number[] = [];
  private insertionSamples: TextInsertionSample[] = [];

  public constructor(domTarget?: TelemetryDomTarget) {
    this.dom = domTarget ?? createDefaultDomTarget();
  }

  public attach(_sessionId: string): void {
    if (this.attached) {
      return;
    }
    this.attached = true;
    this.domUnsubscribers.push(
      this.dom.addVisibilityListener((hidden) => {
        if (hidden) {
          this.handleBlurEvent();
        } else {
          this.handleFocusEvent();
        }
      }),
    );
    this.domUnsubscribers.push(
      this.dom.addWindowBlurFocusListeners(
        () => this.handleBlurEvent(),
        () => this.handleFocusEvent(),
      ),
    );
    this.beginTurn(this.turnNumber, this.difficulty, this.accommodatedTextMode);
  }

  public detach(): void {
    for (const unsub of this.domUnsubscribers) {
      unsub();
    }
    this.domUnsubscribers.length = 0;
    this.attached = false;
  }

  public beginTurn(
    turnNumber: number,
    difficulty: DifficultyLevel,
    accommodatedTextMode: boolean,
  ): void {
    const now = this.dom.nowMs();
    this.turnNumber = turnNumber;
    this.difficulty = difficulty;
    this.accommodatedTextMode = accommodatedTextMode;
    this.turnStartedAtMs = now;
    this.activeBlurStartedAtMs = this.dom.hidden ? now : null;
    this.lastInputTimestampMs = null;
    this.ttftMs = 0;
    this.speechWpm = 0;
    this.pauseEntropy = 0;
    this.totalTypedChars = 0;
    this.blurDurationsMs = [];
    this.interKeyIntervalsMs = [];
    this.insertionSamples = [];
  }

  public recordTextInput(deltaChars: number, source: "TYPED" | "PASTED"): void {
    if (deltaChars <= 0) {
      return;
    }
    const now = this.dom.nowMs();
    if (this.ttftMs === 0 && this.turnStartedAtMs > 0) {
      this.ttftMs = Math.max(0, now - this.turnStartedAtMs);
    }
    const deltaMs =
      this.lastInputTimestampMs !== null ? Math.max(1, now - this.lastInputTimestampMs) : 25;
    this.lastInputTimestampMs = now;

    if (source === "TYPED" && deltaChars <= 4) {
      this.interKeyIntervalsMs.push(deltaMs);
      this.totalTypedChars += deltaChars;
    } else if (source === "TYPED") {
      this.totalTypedChars += deltaChars;
    }

    this.insertionSamples.push({
      deltaChars,
      deltaMs,
      timestampMs: now,
      source,
    });

    if (source === "PASTED" && deltaChars >= 40) {
      const snap = this.snapshotCurrentTurn();
      this.emitSpike("PASTE_BURST", snap);
    }
  }

  public recordSpeechStats(ttftMs: number, wpm: number, pauseEntropy: number): void {
    this.ttftMs = Math.max(0, ttftMs);
    this.speechWpm = Math.max(0, wpm);
    this.pauseEntropy = Math.max(0, pauseEntropy);
  }

  public handleBlurEvent(): void {
    if (!this.attached || this.activeBlurStartedAtMs !== null) {
      return;
    }
    this.activeBlurStartedAtMs = this.dom.nowMs();
  }

  public handleFocusEvent(): void {
    if (!this.attached || this.activeBlurStartedAtMs === null) {
      return;
    }
    const duration = Math.max(0, this.dom.nowMs() - this.activeBlurStartedAtMs);
    this.activeBlurStartedAtMs = null;
    this.blurDurationsMs.push(duration);
    if (duration >= 2000 || this.blurDurationsMs.length >= 2) {
      this.emitSpike("BLUR_SPIKE", this.snapshotCurrentTurn());
    }
  }

  public snapshotCurrentTurn(): ComputedTurnTelemetry {
    const now = this.dom.nowMs();
    const ongoingBlur =
      this.activeBlurStartedAtMs !== null ? Math.max(0, now - this.activeBlurStartedAtMs) : 0;
    const allBlurs =
      ongoingBlur > 0 ? [...this.blurDurationsMs, ongoingBlur] : this.blurDurationsMs;

    return buildTelemetrySnapshotPayload({
      turnNumber: this.turnNumber,
      turnDurationMs: Math.max(1000, now - this.turnStartedAtMs),
      difficulty: this.difficulty,
      blurDurationsMs: allBlurs,
      interKeyIntervalsMs: this.interKeyIntervalsMs,
      insertionSamples: this.insertionSamples,
      totalTypedChars: this.totalTypedChars,
      ttftMs: this.ttftMs,
      speechCadenceWpm: this.speechWpm,
      pauseEntropy: this.pauseEntropy,
      accommodatedTextMode: this.accommodatedTextMode,
    });
  }

  public getLatestWirePayload(): TelemetryWirePayload {
    return this.snapshotCurrentTurn().wirePayload;
  }

  public subscribeSpikes(handler: (alert: TelemetrySpikeAlert) => void): () => void {
    this.spikeListeners.add(handler);
    return () => {
      this.spikeListeners.delete(handler);
    };
  }

  private emitSpike(
    trigger: TelemetrySpikeAlert["trigger"],
    telemetry: ComputedTurnTelemetry,
  ): void {
    for (const handler of this.spikeListeners) {
      handler({ trigger, telemetry });
    }
  }
}
