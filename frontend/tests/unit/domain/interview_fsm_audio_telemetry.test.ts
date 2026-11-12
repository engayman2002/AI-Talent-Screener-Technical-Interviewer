import { describe, expect, it } from "vitest";
import {
  computeAdaptiveJitterCushionMs,
  computeBargeInPlaybackOffsetMs,
  computeDecibelsFs,
  computeRingBufferHeardDurationMs,
  computeRmsEnergy,
  PcmRingBuffer,
  VoiceActivityHysteresisTracker,
} from "../../../src/domain/interview/audio_math";
import {
  createInitialInterviewSnapshot,
  INTERVIEW_FSM_STATES,
  transitionInterviewFsm,
  type InterviewFsmState,
} from "../../../src/domain/interview/fsm";
import {
  assessPasteAndKeystrokeAnomalies,
  buildTelemetrySnapshotPayload,
  computeCadenceAnomalyScore,
  computeFocusAnomalyScore,
  computeKeystrokeStats,
} from "../../../src/domain/interview/telemetry_math";
import {
  InvalidFrontendStateTransitionError,
  ValidationDomainError,
} from "../../../src/domain/shared/errors";

describe("13-State Candidate Interview FSM", () => {
  it("defines the exact 13 states required by ADR-010", () => {
    expect(INTERVIEW_FSM_STATES).toHaveLength(13);
    expect(new Set(INTERVIEW_FSM_STATES)).toEqual(
      new Set([
        "IDLE",
        "AUTHENTICATING",
        "CONNECTING",
        "READY",
        "INTERVIEWER_SPEAKING",
        "CANDIDATE_LISTENING",
        "CANDIDATE_SPEAKING",
        "BARGE_IN_INTERRUPTING",
        "SUBMITTING_TURN",
        "INTERVIEWER_THINKING",
        "RECONNECTING",
        "COMPLETED",
        "TERMINATED_ERROR",
      ]),
    );
  });

  it("traverses full lifecycle including barge-in, reconnect sync, and completion", () => {
    const visited = new Set<InterviewFsmState>();
    let snap = createInitialInterviewSnapshot(false);
    visited.add(snap.state);

    snap = transitionInterviewFsm(snap, {
      type: "START_HANDSHAKE",
      sessionId: "sess-101",
      tenantId: "tenant-a",
    });
    visited.add(snap.state);

    snap = transitionInterviewFsm(snap, { type: "HANDSHAKE_SUCCEEDED" });
    visited.add(snap.state);

    snap = transitionInterviewFsm(snap, {
      type: "SOCKET_SESSION_READY",
      seq: 1,
      turnNumber: 1,
      activeQuestion: "Explain Raft leader election.",
      difficulty: 3,
      checkpointVersion: "v1",
      remainingSeconds: 1800,
      tokensConsumed: 120,
      fencingToken: 1,
    });
    visited.add(snap.state);

    snap = transitionInterviewFsm(snap, { type: "TTS_AUDIO_STARTED", seq: 2 });
    visited.add(snap.state);

    snap = transitionInterviewFsm(snap, { type: "BARGE_IN_TRIGGERED", playedMs: 1350 });
    visited.add(snap.state);
    expect(snap.lastInterruptedPlaybackMs).toBe(1350);

    snap = transitionInterviewFsm(snap, { type: "TTS_FLUSH_ACKED", interruptedAtMs: 1350, seq: 3 });
    visited.add(snap.state);

    snap = transitionInterviewFsm(snap, { type: "CANDIDATE_SPEECH_DETECTED" });
    visited.add(snap.state);

    snap = transitionInterviewFsm(snap, {
      type: "PARTIAL_TRANSCRIPT_RECEIVED",
      seq: 4,
      partialTranscript: "Randomized election timers prevent split votes.",
      incrementAudioChunkCount: true,
    });

    snap = transitionInterviewFsm(snap, { type: "SUBMIT_TURN" });
    visited.add(snap.state);

    snap = transitionInterviewFsm(snap, {
      type: "SERVER_THINKING",
      seq: 5,
      stage: "EVALUATING_TURN",
    });
    visited.add(snap.state);

    // Drop & reconnect mid-thinking
    snap = transitionInterviewFsm(snap, { type: "SOCKET_DISCONNECTED", reason: "wifi_drop" });
    visited.add(snap.state);
    expect(snap.previousActiveState).toBe("INTERVIEWER_THINKING");

    snap = transitionInterviewFsm(snap, {
      type: "SESSION_STATE_SYNC",
      seq: 6,
      turnNumber: 2,
      activeQuestion: "How does Pre-Vote harden Raft?",
      difficulty: 4,
      checkpointVersion: "v2",
      remainingSeconds: 1710,
      tokensConsumed: 380,
      fencingToken: 2,
      isCompleted: false,
    });
    expect(snap.state).toBe("INTERVIEWER_THINKING");

    snap = transitionInterviewFsm(snap, {
      type: "INTERVIEWER_QUESTION_RECEIVED",
      seq: 7,
      turnNumber: 2,
      questionText: "How does Pre-Vote harden Raft?",
      difficulty: 4,
      routingAction: "ESCALATE_DIFFICULTY",
      checkpointVersion: "v2",
    });

    snap = transitionInterviewFsm(snap, { type: "TURN_READY_FOR_CANDIDATE" });
    snap = transitionInterviewFsm(snap, { type: "UPDATE_SUPPLEMENTAL_TEXT", text: "Pre-Vote avoids term inflation." });
    snap = transitionInterviewFsm(snap, { type: "SUBMIT_TURN" });
    snap = transitionInterviewFsm(snap, {
      type: "SESSION_COMPLETED",
      seq: 8,
      closingMessage: "Thank you for interviewing.",
      tokensConsumed: 520,
    });
    visited.add(snap.state);

    // Also test TERMINATED_ERROR path from a fresh session
    let errSnap = transitionInterviewFsm(createInitialInterviewSnapshot(), {
      type: "START_HANDSHAKE",
      sessionId: "sess-err",
    });
    errSnap = transitionInterviewFsm(errSnap, {
      type: "HANDSHAKE_FAILED",
      errorCode: "SESSION_TICKET_EXPIRED",
      errorDetail: "Ticket expired.",
    });
    visited.add(errSnap.state);

    expect(visited.size).toBe(13);
  });

  it("rejects empty turn submission and illegal state transitions", () => {
    const ready = transitionInterviewFsm(
      transitionInterviewFsm(
        transitionInterviewFsm(createInitialInterviewSnapshot(), {
          type: "START_HANDSHAKE",
          sessionId: "sess-guard",
        }),
        { type: "HANDSHAKE_SUCCEEDED" },
      ),
      {
        type: "SOCKET_SESSION_READY",
        seq: 1,
        turnNumber: 1,
        activeQuestion: "Q1",
        difficulty: 2,
        checkpointVersion: "v1",
        remainingSeconds: 1200,
        tokensConsumed: 0,
        fencingToken: 1,
      },
    );

    expect(() =>
      transitionInterviewFsm(ready, { type: "SUBMIT_TURN" }),
    ).toThrow(InvalidFrontendStateTransitionError);

    expect(() =>
      transitionInterviewFsm(createInitialInterviewSnapshot(), {
        type: "HANDSHAKE_SUCCEEDED",
      }),
    ).toThrow(InvalidFrontendStateTransitionError);
  });
});

describe("Web Audio Ring-Buffer, VAD Hysteresis & Barge-In Math", () => {
  it("handles circular wrap-around, overflow counting, zero-pad draining, and immediate flush", () => {
    expect(() => new PcmRingBuffer(0)).toThrow(ValidationDomainError);

    const ring = new PcmRingBuffer(4, 16_000);
    expect(ring.enqueue(new Float32Array([0.1, 0.2, 0.3]))).toBe(0);
    expect(ring.unreadSamples).toBe(3);

    // Overwrite 2 oldest samples (capacity 4, unread 3 + new 3 = 6 -> drop 2)
    const dropped = ring.enqueue(new Float32Array([0.4, 0.5, 0.6]));
    expect(dropped).toBe(2);
    expect(ring.unreadSamples).toBe(4);

    const out = new Float32Array(6);
    const copied = ring.drainInto(out);
    expect(copied).toBe(4);
    expect(Array.from(out.slice(0, 4)).map((v) => Number(v.toFixed(2)))).toEqual([
      0.3, 0.4, 0.5, 0.6,
    ]);
    expect(out[4]).toBe(0);
    expect(out[5]).toBe(0);

    ring.enqueue(new Float32Array([0.9, -0.9]));
    expect(ring.flushImmediate()).toBe(2);
    expect(ring.unreadSamples).toBe(0);
  });

  it("computes RMS, dBFS, jitter cushion, barge-in offset, and 280ms VAD hysteresis", () => {
    const rms = computeRmsEnergy(new Float32Array([0.5, -0.5, 0.5, -0.5]));
    expect(rms).toBeCloseTo(0.5, 5);
    expect(computeDecibelsFs(rms)).toBeCloseTo(-6.0206, 2);
    expect(computeDecibelsFs(0)).toBe(-60);

    expect(computeAdaptiveJitterCushionMs(0)).toBe(40);
    expect(computeAdaptiveJitterCushionMs(20)).toBe(90);
    expect(computeAdaptiveJitterCushionMs(200)).toBe(160);

    expect(
      computeBargeInPlaybackOffsetMs({
        totalDurationMs: 2500,
        anchorTimeSeconds: 10,
        interruptTimeSeconds: 11.42,
      }),
    ).toBeCloseTo(1420, 4);

    expect(
      computeRingBufferHeardDurationMs({
        totalEnqueuedMs: 2000,
        unreadSamples: 24_000,
        sampleRateHz: 48_000,
      }),
    ).toBeCloseTo(1500, 4);

    const vad = new VoiceActivityHysteresisTracker(0.035, 280);
    expect(vad.pushFrame(0.08, 20)).toBe(false);
    // Transient silence resets hysteresis window
    expect(vad.pushFrame(0.005, 20)).toBe(false);
    expect(vad.accumulatedVoicedMs).toBe(0);

    // 13 x 20ms = 260ms -> not yet triggered; 14th frame = 280ms -> triggers!
    for (let i = 0; i < 13; i += 1) {
      expect(vad.pushFrame(0.1, 20)).toBe(false);
    }
    expect(vad.pushFrame(0.1, 20)).toBe(true);
  });
});

describe("Kinematic Telemetry, Paste Burst & Accommodated Text Exemption", () => {
  it("computes keystroke CV, detects >=40 char rapid paste burst, and computes S_focus & S_cadence", () => {
    const lowVarStats = computeKeystrokeStats(new Array(15).fill(80));
    expect(lowVarStats.cvIki).toBe(0);

    const paste = assessPasteAndKeystrokeAnomalies(
      [{ deltaChars: 120, deltaMs: 40, timestampMs: 5000, source: "PASTED" }],
      lowVarStats,
      100,
    );
    expect(paste.pasteDetected).toBe(true);
    expect(paste.pasteCharCount).toBe(120);
    expect(paste.keystrokeAnomalyDetected).toBe(true);
    expect(paste.pasteAnomalyScore).toBeGreaterThan(0.9);

    const cleanFocus = computeFocusAnomalyScore([], 30_000);
    const highFocus = computeFocusAnomalyScore([4000, 5000, 3000], 20_000);
    expect(cleanFocus).toBe(0);
    expect(highFocus).toBeGreaterThan(0.8);

    const suspiciousCadence = computeCadenceAnomalyScore({
      ttftMs: 12_000,
      speechCadenceWpm: 210,
      pauseEntropy: 0.05,
      difficulty: 2,
      accommodatedTextMode: false,
    });
    expect(suspiciousCadence).toBeGreaterThan(0.8);

    // Exact accommodated text mode invariant (Phase 7 Section 3.5)
    const exemptCadence = computeCadenceAnomalyScore({
      ttftMs: 12_000,
      speechCadenceWpm: 210,
      pauseEntropy: 0.05,
      difficulty: 2,
      accommodatedTextMode: true,
    });
    expect(exemptCadence).toBe(0);

    const payload = buildTelemetrySnapshotPayload({
      turnNumber: 2,
      turnDurationMs: 25_000,
      difficulty: 4,
      blurDurationsMs: [3500],
      interKeyIntervalsMs: [90, 140, 110],
      insertionSamples: [{ deltaChars: 65, deltaMs: 25, timestampMs: 1000, source: "PASTED" }],
      totalTypedChars: 15,
      ttftMs: 6000,
      speechCadenceWpm: 165,
      pauseEntropy: 0.2,
      accommodatedTextMode: true,
    });
    expect(payload.wirePayload.external_paste_detected).toBe(true);
    expect(payload.cadenceAnomalyScore).toBe(0);
  });
});
