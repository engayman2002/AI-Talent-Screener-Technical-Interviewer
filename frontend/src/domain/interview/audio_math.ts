/**
 * Pure Web Audio Ring-Buffer Arithmetic, RMS VAD Hysteresis & Barge-In Truncation Math.
 * Zero AudioContext / browser global dependencies.
 */

import { ValidationDomainError } from "../shared/errors";

export const DEFAULT_SAMPLE_RATE_HZ = 48_000;
export const DEFAULT_BARGE_IN_VAD_ONSET_MS = 280;

/**
 * Circular PCM Float32 ring buffer with deterministic pointer wrap-around
 * and unread sample accounting for lock-free / off-main-thread parity.
 */
export class PcmRingBuffer {
  private readonly buffer: Float32Array;
  private writeIndex = 0;
  private readIndex = 0;
  private available = 0;
  public readonly capacity: number;
  public readonly sampleRateHz: number;

  public constructor(capacitySamples: number, sampleRateHz = DEFAULT_SAMPLE_RATE_HZ) {
    if (!Number.isInteger(capacitySamples) || capacitySamples <= 0) {
      throw new ValidationDomainError(
        "Ring buffer capacitySamples must be a positive integer.",
        "INVALID_RING_BUFFER_CAPACITY",
      );
    }
    if (!Number.isFinite(sampleRateHz) || sampleRateHz <= 0) {
      throw new ValidationDomainError(
        "sampleRateHz must be strictly positive.",
        "INVALID_SAMPLE_RATE",
      );
    }
    this.capacity = capacitySamples;
    this.sampleRateHz = sampleRateHz;
    this.buffer = new Float32Array(capacitySamples);
  }

  public get unreadSamples(): number {
    return this.available;
  }

  public get writeCursor(): number {
    return this.writeIndex;
  }

  public get readCursor(): number {
    return this.readIndex;
  }

  public get bufferedDurationMs(): number {
    return (this.available / this.sampleRateHz) * 1000;
  }

  /**
   * Write incoming PCM samples into the circular buffer.
   * Overwrites oldest unread samples if incoming frames exceed free space.
   * Returns count of dropped oldest samples due to overflow.
   */
  public enqueue(samples: Float32Array): number {
    let overwritten = 0;
    for (let i = 0; i < samples.length; i += 1) {
      const sample = samples[i];
      if (sample === undefined) {
        continue;
      }
      if (this.available === this.capacity) {
        // Advance read pointer cleanly on overwrite
        this.readIndex = (this.readIndex + 1) % this.capacity;
        this.available -= 1;
        overwritten += 1;
      }
      this.buffer[this.writeIndex] = sample;
      this.writeIndex = (this.writeIndex + 1) % this.capacity;
      this.available += 1;
    }
    return overwritten;
  }

  /**
   * Read up to `target.length` samples into `target`, zero-padding any remainder.
   * Returns exact count of real audio samples copied.
   */
  public drainInto(target: Float32Array): number {
    const toRead = Math.min(target.length, this.available);
    for (let i = 0; i < toRead; i += 1) {
      const val = this.buffer[this.readIndex];
      target[i] = val ?? 0;
      this.readIndex = (this.readIndex + 1) % this.capacity;
      this.available -= 1;
    }
    for (let i = toRead; i < target.length; i += 1) {
      target[i] = 0;
    }
    return toRead;
  }

  /** Immediately clear all unread samples on barge-in interruption and return flushed count. */
  public flushImmediate(): number {
    const flushed = this.available;
    this.readIndex = this.writeIndex;
    this.available = 0;
    return flushed;
  }
}

/** Exact Root-Mean-Square acoustic energy E_rms = sqrt((1/N) * sum(x_i^2)). */
export function computeRmsEnergy(samples: Float32Array): number {
  if (samples.length === 0) {
    return 0;
  }
  let sumSq = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const x = samples[i] ?? 0;
    sumSq += x * x;
  }
  return Math.sqrt(sumSq / samples.length);
}

/** Convert linear RMS amplitude in [0, 1] into dBFS bounded to [-60, 0]. */
export function computeDecibelsFs(rms: number): number {
  if (rms <= 1e-6) {
    return -60;
  }
  const db = 20 * Math.log10(Math.min(1, rms));
  return Math.max(-60, Math.min(0, db));
}

/** Adaptive TTS jitter cushion B_cushion = clamp(40 + 2.5 * sigma_net, 40, 160) ms. */
export function computeAdaptiveJitterCushionMs(jitterStdDevMs: number): number {
  const safeJitter = Math.max(0, Number.isFinite(jitterStdDevMs) ? jitterStdDevMs : 0);
  return Math.max(40, Math.min(160, 40 + 2.5 * safeJitter));
}

export interface PlaybackOffsetParams {
  /** Total duration D_total_ms of enqueued/synthesized audio in milliseconds. */
  readonly totalDurationMs: number;
  /** AudioContext playback anchor timestamp t_anchor in seconds. */
  readonly anchorTimeSeconds: number;
  /** Hardware clock interruption timestamp t_interrupt in seconds. */
  readonly interruptTimeSeconds: number;
}

/**
 * Exact wall-clock barge-in truncation offset:
 * t_played_ms = max(0, min(D_total_ms, (t_interrupt - t_anchor) * 1000)).
 */
export function computeBargeInPlaybackOffsetMs(params: PlaybackOffsetParams): number {
  const safeTotal = Math.max(0, params.totalDurationMs);
  const elapsedMs = (params.interruptTimeSeconds - params.anchorTimeSeconds) * 1000;
  return Math.max(0, Math.min(safeTotal, elapsedMs));
}

export interface RingBufferHeardParams {
  readonly totalEnqueuedMs: number;
  readonly unreadSamples: number;
  readonly sampleRateHz?: number;
}

/**
 * Exact ring-buffer heard duration accounting for unplayed buffered audio:
 * t_heard = max(0, T_enqueued - (N_unread / f_s) * 1000).
 */
export function computeRingBufferHeardDurationMs(params: RingBufferHeardParams): number {
  const fs = params.sampleRateHz ?? DEFAULT_SAMPLE_RATE_HZ;
  if (fs <= 0) {
    return 0;
  }
  const unreadMs = (Math.max(0, params.unreadSamples) / fs) * 1000;
  return Math.max(0, params.totalEnqueuedMs - unreadMs);
}

/**
 * Stateful pure domain VAD onset tracker enforcing sustained speech hysteresis
 * (default 280 ms voiced energy) before confirming candidate barge-in.
 */
export class VoiceActivityHysteresisTracker {
  private voicedAccumulatedMs = 0;
  public readonly rmsThreshold: number;
  public readonly onsetWindowMs: number;

  public constructor(
    rmsThreshold = 0.035,
    onsetWindowMs = DEFAULT_BARGE_IN_VAD_ONSET_MS,
  ) {
    this.rmsThreshold = rmsThreshold;
    this.onsetWindowMs = Math.max(20, onsetWindowMs);
  }

  public get accumulatedVoicedMs(): number {
    return this.voicedAccumulatedMs;
  }

  /**
   * Process one audio frame of `frameDurationMs` and return true iff sustained
   * voiced energy meets or exceeds `onsetWindowMs`.
   */
  public pushFrame(rmsEnergy: number, frameDurationMs: number): boolean {
    if (rmsEnergy >= this.rmsThreshold) {
      this.voicedAccumulatedMs += Math.max(0, frameDurationMs);
      return this.voicedAccumulatedMs >= this.onsetWindowMs;
    }
    this.voicedAccumulatedMs = 0;
    return false;
  }

  public reset(): void {
    this.voicedAccumulatedMs = 0;
  }
}
