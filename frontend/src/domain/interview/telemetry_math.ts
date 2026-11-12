/**
 * Pure Client-Side Kinematic, Clipboard Paste Burst & Focus Integrity Telemetry Math.
 * Implements Phase 7 Section 2.4 equations without DOM or privacy-invasive raw logging.
 */

import type { DifficultyLevel } from "../shared/types";

export const ETA_FOCUS_RATIO = 3.5;
export const ETA_FOCUS_SWITCH = 0.4;
export const PASTE_BURST_CHAR_THRESHOLD = 40;
export const PASTE_BURST_WINDOW_MS = 150;
export const KEYSTROKE_LOW_CV_THRESHOLD = 0.1;

export interface TextInsertionSample {
  readonly deltaChars: number;
  readonly deltaMs: number;
  readonly timestampMs: number;
  readonly source: "TYPED" | "PASTED" | "UNKNOWN";
}

export interface KeystrokeStats {
  readonly sampleCount: number;
  readonly meanIkiMs: number;
  readonly stdDevIkiMs: number;
  readonly cvIki: number;
  readonly shannonEntropyBits: number;
}

export interface PasteBurstAssessment {
  readonly pasteDetected: boolean;
  readonly pasteCharCount: number;
  readonly keystrokeAnomalyDetected: boolean;
  readonly pasteAnomalyScore: number;
}

export interface TurnTelemetryInput {
  readonly turnNumber: number;
  readonly turnDurationMs: number;
  readonly difficulty: DifficultyLevel;
  readonly blurDurationsMs: readonly number[];
  readonly interKeyIntervalsMs: readonly number[];
  readonly insertionSamples: readonly TextInsertionSample[];
  readonly totalTypedChars: number;
  readonly ttftMs: number;
  readonly speechCadenceWpm: number;
  readonly pauseEntropy: number;
  readonly accommodatedTextMode: boolean;
  readonly populationPriorLagMs?: number;
  readonly populationLagStdMs?: number;
}

export interface TelemetryWirePayload {
  readonly turn_number: number;
  readonly focus_loss_count: number;
  readonly focus_loss_duration_ms: number;
  readonly external_paste_detected: boolean;
  readonly paste_char_count: number;
  readonly keystroke_anomaly_detected: boolean;
  readonly ttft_ms: number;
  readonly total_duration_ms: number;
  readonly speech_cadence_wpm: number;
  readonly pause_entropy: number;
}

export interface ComputedTurnTelemetry {
  readonly wirePayload: TelemetryWirePayload;
  readonly focusAnomalyScore: number;
  readonly pasteAnomalyScore: number;
  readonly cadenceAnomalyScore: number;
  readonly compositeClientRisk: number;
  readonly keystrokeStats: KeystrokeStats;
}

function clamp01(val: number): number {
  if (!Number.isFinite(val)) {
    return 0;
  }
  return Math.max(0, Math.min(1, val));
}

function sigmoid(x: number): number {
  return 1 / (1 + Math.exp(-Math.max(-30, Math.min(30, x))));
}

/**
 * Compute mean, standard deviation, Coefficient of Variation (CV_iki),
 * and binned Shannon entropy (bits) of inter-keystroke intervals.
 */
export function computeKeystrokeStats(interKeyIntervalsMs: readonly number[]): KeystrokeStats {
  const valid = interKeyIntervalsMs.filter((v) => Number.isFinite(v) && v >= 0);
  const n = valid.length;
  if (n === 0) {
    return {
      sampleCount: 0,
      meanIkiMs: 0,
      stdDevIkiMs: 0,
      cvIki: 0,
      shannonEntropyBits: 0,
    };
  }

  let sum = 0;
  for (let i = 0; i < n; i += 1) {
    sum += valid[i] ?? 0;
  }
  const mean = sum / n;

  let varSum = 0;
  for (let i = 0; i < n; i += 1) {
    const diff = (valid[i] ?? 0) - mean;
    varSum += diff * diff;
  }
  const stdDev = Math.sqrt(varSum / n);
  const cvIki = mean > 1e-6 ? stdDev / mean : 0;

  // Quantize into 25 ms histogram bins for Shannon entropy H = -sum(p log2 p).
  const binCounts = new Map<number, number>();
  for (let i = 0; i < n; i += 1) {
    const bin = Math.min(40, Math.floor((valid[i] ?? 0) / 25));
    binCounts.set(bin, (binCounts.get(bin) ?? 0) + 1);
  }
  let entropy = 0;
  for (const count of binCounts.values()) {
    const p = count / n;
    if (p > 0) {
      entropy -= p * Math.log2(p);
    }
  }

  return {
    sampleCount: n,
    meanIkiMs: mean,
    stdDevIkiMs: stdDev,
    cvIki,
    shannonEntropyBits: entropy,
  };
}

/**
 * Detect clipboard paste bursts (>= 40 chars inside < 150 ms) and synthetic macro keystrokes.
 */
export function assessPasteAndKeystrokeAnomalies(
  insertionSamples: readonly TextInsertionSample[],
  keystrokeStats: KeystrokeStats,
  totalTypedChars: number,
  thresholdChars = PASTE_BURST_CHAR_THRESHOLD,
  maxWindowMs = PASTE_BURST_WINDOW_MS,
): PasteBurstAssessment {
  let pasteDetected = false;
  let pasteCharCount = 0;
  let maxPasteTerm = 0;

  for (let i = 0; i < insertionSamples.length; i += 1) {
    const sample = insertionSamples[i];
    if (sample === undefined || sample.deltaChars <= 0) {
      continue;
    }
    const safeDeltaMs = Math.max(1, sample.deltaMs);
    const velocityCharsPerSec = (sample.deltaChars / safeDeltaMs) * 1000;
    const isBurst =
      sample.deltaChars >= thresholdChars &&
      (sample.deltaMs < maxWindowMs || sample.source === "PASTED");

    if (isBurst) {
      pasteDetected = true;
      pasteCharCount += sample.deltaChars;
    }
    if (velocityCharsPerSec > 15) {
      const term = sigmoid((sample.deltaChars - thresholdChars) / 25);
      if (term > maxPasteTerm) {
        maxPasteTerm = term;
      }
    }
  }

  const keystrokeAnomaly =
    keystrokeStats.sampleCount >= 12 &&
    totalTypedChars > 60 &&
    keystrokeStats.cvIki < KEYSTROKE_LOW_CV_THRESHOLD;

  const macroTerm = keystrokeAnomaly ? 0.5 : 0;
  const pasteAnomalyScore = clamp01(maxPasteTerm + macroTerm);

  return {
    pasteDetected,
    pasteCharCount,
    keystrokeAnomalyDetected: keystrokeAnomaly,
    pasteAnomalyScore,
  };
}

/**
 * Compute Focus Anomaly Score S_focus,t in [0, 1] per Phase 7 Section 2.4.1:
 * S_focus = 1 - exp(-3.5 * (sum(blur_ms) / T_turn_ms) - 0.40 * max(0, m - 1))
 */
export function computeFocusAnomalyScore(
  blurDurationsMs: readonly number[],
  turnDurationMs: number,
): number {
  const m = blurDurationsMs.length;
  if (m === 0) {
    return 0;
  }
  let totalBlur = 0;
  for (let i = 0; i < m; i += 1) {
    totalBlur += Math.max(0, blurDurationsMs[i] ?? 0);
  }
  const safeTurnMs = Math.max(1000, turnDurationMs);
  const ratio = totalBlur / safeTurnMs;
  const score = 1 - Math.exp(-ETA_FOCUS_RATIO * ratio - ETA_FOCUS_SWITCH * Math.max(0, m - 1));
  return clamp01(score);
}

const DIFFICULTY_BASELINE_LAG_MS: Readonly<Record<DifficultyLevel, number>> = {
  1: 1800,
  2: 2300,
  3: 2800,
  4: 3400,
  5: 4200,
};

/**
 * Compute Vocal Initiation Lag vs. Post-Lag Reading Uniformity Anomaly S_cadence,t in [0, 1].
 * Guaranteed 0 when accommodatedTextMode is active (Phase 7 Section 3.5).
 */
export function computeCadenceAnomalyScore(params: {
  readonly ttftMs: number;
  readonly speechCadenceWpm: number;
  readonly pauseEntropy: number;
  readonly difficulty: DifficultyLevel;
  readonly accommodatedTextMode: boolean;
  readonly populationPriorLagMs?: number;
  readonly populationLagStdMs?: number;
}): number {
  if (params.accommodatedTextMode || params.speechCadenceWpm <= 0) {
    return 0;
  }
  const baselineLag =
    params.populationPriorLagMs ?? DIFFICULTY_BASELINE_LAG_MS[params.difficulty];
  const stdLag = Math.max(250, params.populationLagStdMs ?? 1100);
  const zLag = (Math.max(0, params.ttftMs) - baselineLag) / stdLag;
  const uUniform = params.speechCadenceWpm / (1 + Math.max(0, params.pauseEntropy));
  const uBaseline = 110; // Spontaneous conversational reading baseline
  const lagFactor = Math.max(0, zLag - 1.5);
  const uniformityFactor = Math.max(0, (uUniform - uBaseline) / uBaseline);
  if (lagFactor === 0 || uniformityFactor === 0) {
    return 0;
  }
  return clamp01(sigmoid(3.2 * lagFactor * uniformityFactor - 1.5));
}

/**
 * Aggregate raw behavioral, focus, keyboard, and vocal signals into
 * a backend-compatible TelemetrySnapshotPayload and normalized risk vector.
 */
export function buildTelemetrySnapshotPayload(
  input: TurnTelemetryInput,
): ComputedTurnTelemetry {
  const safeTurnNumber = Math.max(1, Math.round(input.turnNumber));
  const safeTotalMs = Math.max(0, Math.round(input.turnDurationMs));
  const safeTtft = Math.min(safeTotalMs, Math.max(0, Math.round(input.ttftMs)));
  const totalBlurMs = input.blurDurationsMs.reduce(
    (acc, dur) => acc + Math.max(0, Math.round(dur)),
    0,
  );

  const keystrokeStats = computeKeystrokeStats(input.interKeyIntervalsMs);
  const paste = assessPasteAndKeystrokeAnomalies(
    input.insertionSamples,
    keystrokeStats,
    input.totalTypedChars,
  );
  const focusScore = computeFocusAnomalyScore(input.blurDurationsMs, safeTotalMs);
  const cadenceScore = computeCadenceAnomalyScore({
    ttftMs: safeTtft,
    speechCadenceWpm: input.speechCadenceWpm,
    pauseEntropy: input.pauseEntropy,
    difficulty: input.difficulty,
    accommodatedTextMode: input.accommodatedTextMode,
    ...(input.populationPriorLagMs !== undefined
      ? { populationPriorLagMs: input.populationPriorLagMs }
      : {}),
    ...(input.populationLagStdMs !== undefined
      ? { populationLagStdMs: input.populationLagStdMs }
      : {}),
  });

  const composite = clamp01(
    0.4 * focusScore + 0.35 * paste.pasteAnomalyScore + 0.25 * cadenceScore,
  );

  const wirePayload: TelemetryWirePayload = {
    turn_number: safeTurnNumber,
    focus_loss_count: input.blurDurationsMs.length,
    focus_loss_duration_ms: totalBlurMs,
    external_paste_detected: paste.pasteDetected,
    paste_char_count: paste.pasteCharCount,
    keystroke_anomaly_detected: paste.keystrokeAnomalyDetected,
    ttft_ms: safeTtft,
    total_duration_ms: safeTotalMs,
    speech_cadence_wpm: Math.max(0, Math.min(600, input.speechCadenceWpm)),
    pause_entropy: Math.max(0, Math.min(100, input.pauseEntropy)),
  };

  return {
    wirePayload,
    focusAnomalyScore: focusScore,
    pasteAnomalyScore: paste.pasteAnomalyScore,
    cadenceAnomalyScore: cadenceScore,
    compositeClientRisk: composite,
    keystrokeStats,
  };
}
