/**
 * Live Interview Session Coordinator (Frontend Application Layer).
 * Orchestrates the 13-State Candidate Interview FSM, WebSocket protocol,
 * Web Audio ring-buffer barge-in, and non-intrusive integrity telemetry.
 */

import { computeAdaptiveJitterCushionMs } from "../../domain/interview/audio_math";
import {
  createInitialInterviewSnapshot,
  transitionInterviewFsm,
  type InterviewPortalEvent,
  type InterviewPortalSnapshot,
} from "../../domain/interview/fsm";
import type { DifficultyLevel, RoutingAction } from "../../domain/shared/types";
import type {
  AudioEnginePort,
  IntegrityTelemetryCollectorPort,
  InterviewWebSocketPort,
  ServerWsFrameEnvelope,
  TelemetrySpikeAlert,
} from "../ports/outbound";
import { ObservableStore, type UnsubscribeFn } from "../shared/store";

export interface StartInterviewSessionCommand {
  readonly sessionId: string;
  readonly tenantId?: string;
  readonly wsUrl: string;
  readonly ticketToken: string;
  readonly accommodatedTextMode?: boolean;
}

function asNumber(val: unknown, fallback: number): number {
  return typeof val === "number" && Number.isFinite(val) ? val : fallback;
}

function asString(val: unknown, fallback = ""): string {
  return typeof val === "string" ? val : fallback;
}

function asDifficulty(val: unknown, fallback: DifficultyLevel = 3): DifficultyLevel {
  if (val === 1 || val === 2 || val === 3 || val === 4 || val === 5) {
    return val;
  }
  return fallback;
}

function asRoutingAction(val: unknown): RoutingAction {
  switch (val) {
    case "ESCALATE_DIFFICULTY":
    case "SOCRATIC_DEPTH_PROBE":
    case "SOCRATIC_AUTHENTICITY_PROBE":
    case "TRANSITION_NEXT_COMPETENCY":
    case "CONCLUDE_INTERVIEW":
      return val;
    default:
      return "ESCALATE_DIFFICULTY";
  }
}

export class LiveInterviewSessionCoordinator {
  private readonly store: ObservableStore<InterviewPortalSnapshot>;
  private readonly ws: InterviewWebSocketPort;
  private readonly audio: AudioEnginePort;
  private readonly telemetry: IntegrityTelemetryCollectorPort;
  private readonly unsubscribers: UnsubscribeFn[] = [];
  private jitterCushionMs = computeAdaptiveJitterCushionMs(8);

  public constructor(params: {
    readonly ws: InterviewWebSocketPort;
    readonly audio: AudioEnginePort;
    readonly telemetry: IntegrityTelemetryCollectorPort;
    readonly accommodatedTextMode?: boolean;
  }) {
    this.ws = params.ws;
    this.audio = params.audio;
    this.telemetry = params.telemetry;
    this.store = new ObservableStore(
      createInitialInterviewSnapshot(params.accommodatedTextMode ?? false),
    );
    this.bindInfrastructureSubscriptions();
  }

  public readonly getSnapshot = (): InterviewPortalSnapshot => this.store.getSnapshot();

  public readonly subscribe = (listener: () => void): UnsubscribeFn =>
    this.store.subscribe(listener);

  public getJitterCushionMs(): number {
    return this.jitterCushionMs;
  }

  public setAccommodatedTextMode(enabled: boolean): void {
    this.dispatch({ type: "SET_ACCOMMODATED_TEXT_MODE", enabled });
  }

  public updateSupplementalText(text: string, source: "TYPED" | "PASTED" = "TYPED"): void {
    const prevLen = this.store.getSnapshot().draftSupplementalText.length;
    const deltaChars = Math.max(0, text.length - prevLen);
    this.dispatch({ type: "UPDATE_SUPPLEMENTAL_TEXT", text });
    if (deltaChars > 0) {
      this.telemetry.recordTextInput(deltaChars, source);
    }
  }

  public async startSession(cmd: StartInterviewSessionCommand): Promise<void> {
    if (cmd.accommodatedTextMode !== undefined) {
      this.dispatch({
        type: "SET_ACCOMMODATED_TEXT_MODE",
        enabled: cmd.accommodatedTextMode,
      });
    }
    this.dispatch({
      type: "START_HANDSHAKE",
      sessionId: cmd.sessionId,
      ...(cmd.tenantId !== undefined ? { tenantId: cmd.tenantId } : {}),
    });

    try {
      await this.audio.requestMicrophone();
      this.telemetry.attach(cmd.sessionId);
      this.dispatch({ type: "HANDSHAKE_SUCCEEDED" });

      await this.ws.connect({
        url: cmd.wsUrl,
        sessionId: cmd.sessionId,
        authToken: cmd.ticketToken,
        lastServerSeq: this.store.getSnapshot().lastServerSeq,
      });

      this.audio.startCapture({
        onFrameCaptured: () => {
          // Energy metering stays off the React tree
        },
        onVadSpeechStart: () => {
          if (this.store.getSnapshot().state === "CANDIDATE_LISTENING") {
            this.dispatch({ type: "CANDIDATE_SPEECH_DETECTED" });
          }
        },
        onVadSpeechEnd: () => {
          if (this.store.getSnapshot().state === "CANDIDATE_SPEAKING") {
            this.dispatch({ type: "CANDIDATE_SILENCE_PAUSED" });
          }
        },
        onBargeInDetected: (playedMs: number) => {
          void this.triggerBargeIn(playedMs);
        },
      });

      await this.ws.sendFrame({
        event_type: "session.join",
        payload: {
          session_id: cmd.sessionId,
          last_received_seq: this.store.getSnapshot().lastServerSeq,
        },
      });
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Failed to initialize live interview.";
      if (this.store.getSnapshot().state === "AUTHENTICATING") {
        this.dispatch({
          type: "HANDSHAKE_FAILED",
          errorCode: "INTERVIEW_HANDSHAKE_FAILED",
          errorDetail: detail,
        });
      } else if (this.store.getSnapshot().state !== "TERMINATED_ERROR") {
        this.dispatch({
          type: "FATAL_ERROR",
          errorCode: "INTERVIEW_INIT_FAILED",
          errorDetail: detail,
        });
      }
      throw err;
    }
  }

  public async triggerBargeIn(explicitPlayedMs?: number): Promise<number> {
    const snap = this.store.getSnapshot();
    if (snap.state !== "INTERVIEWER_SPEAKING") {
      return 0;
    }
    const playedMs = explicitPlayedMs ?? this.audio.flushPlayback();
    this.audio.setAiSpeakingState(false);
    this.dispatch({ type: "BARGE_IN_TRIGGERED", playedMs });
    await this.ws.sendFrame({
      event_type: "candidate.barge_in",
      payload: {
        turn_number: snap.turnNumber,
        interrupted_at_ms: Math.round(playedMs),
      },
    });
    return playedMs;
  }

  public async submitUtterance(utteranceOverride?: string): Promise<void> {
    const preSnap = this.store.getSnapshot();
    this.dispatch({
      type: "SUBMIT_TURN",
      ...(utteranceOverride !== undefined ? { utteranceOverride } : {}),
    });

    const postSnap = this.store.getSnapshot();
    const lastEntry = postSnap.transcriptLedger[postSnap.transcriptLedger.length - 1];
    const utteranceText = lastEntry?.text ?? "";
    const snapTelemetry = this.telemetry.snapshotCurrentTurn();

    await this.ws.sendFrame({
      event_type: "candidate.utterance",
      payload: {
        ...snapTelemetry.wirePayload,
        utterance: utteranceText,
        turn_number: preSnap.turnNumber,
        checkpoint_version: preSnap.checkpointVersion,
        remaining_seconds: preSnap.remainingSeconds,
        synthesize_voice: !preSnap.accommodatedTextMode,
        response_latency_ms: snapTelemetry.wirePayload.ttft_ms,
      },
    });
  }

  public async flushPeriodicTelemetry(): Promise<void> {
    const snap = this.store.getSnapshot();
    if (
      snap.state !== "CANDIDATE_LISTENING" &&
      snap.state !== "CANDIDATE_SPEAKING" &&
      snap.state !== "INTERVIEWER_SPEAKING"
    ) {
      return;
    }
    const telemetry = this.telemetry.snapshotCurrentTurn();
    await this.ws.sendFrame({
      event_type: "telemetry.snapshot",
      payload: { ...telemetry.wirePayload },
    });
  }

  public async terminateSession(reason: string): Promise<void> {
    await this.ws.sendFrame({
      event_type: "session.terminate",
      payload: { reason },
    });
  }

  public markTurnInputReady(): void {
    const snap = this.store.getSnapshot();
    if (snap.state === "INTERVIEWER_SPEAKING" || snap.state === "READY") {
      this.audio.setAiSpeakingState(false);
      this.dispatch({ type: "TURN_READY_FOR_CANDIDATE" });
      this.telemetry.beginTurn(snap.turnNumber, snap.difficulty, snap.accommodatedTextMode);
    }
  }

  public handleServerFrame(frame: ServerWsFrameEnvelope): void {
    const snap = this.store.getSnapshot();
    const p = frame.payload;

    switch (frame.event_type) {
      case "session.ready": {
        if (snap.state === "RECONNECTING") {
          this.dispatch({
            type: "SESSION_STATE_SYNC",
            seq: frame.seq,
            turnNumber: asNumber(p["turn_number"], frame.turn_number),
            activeQuestion: asString(p["active_question"], snap.activeQuestion),
            difficulty: asDifficulty(p["difficulty"], snap.difficulty),
            checkpointVersion: asString(p["checkpoint_version"], snap.checkpointVersion),
            remainingSeconds: asNumber(p["remaining_seconds"], snap.remainingSeconds),
            tokensConsumed: asNumber(p["tokens_consumed"], snap.tokensConsumed),
            fencingToken: asNumber(p["fencing_token"], snap.fencingToken),
            isCompleted: Boolean(p["is_completed"]),
          });
        } else {
          this.dispatch({
            type: "SOCKET_SESSION_READY",
            seq: frame.seq,
            turnNumber: asNumber(p["turn_number"], 1),
            activeQuestion: asString(p["active_question"], ""),
            difficulty: asDifficulty(p["difficulty"], 3),
            checkpointVersion: asString(p["checkpoint_version"], ""),
            remainingSeconds: asNumber(p["remaining_seconds"], 1800),
            tokensConsumed: asNumber(p["tokens_consumed"], 0),
            fencingToken: asNumber(p["fencing_token"], 1),
          });
        }
        const refreshed = this.store.getSnapshot();
        this.telemetry.beginTurn(
          refreshed.turnNumber,
          refreshed.difficulty,
          refreshed.accommodatedTextMode,
        );
        break;
      }

      case "session.state_sync": {
        this.dispatch({
          type: "SESSION_STATE_SYNC",
          seq: frame.seq,
          turnNumber: asNumber(p["turn_number"], snap.turnNumber),
          activeQuestion: asString(p["active_question"], snap.activeQuestion),
          difficulty: asDifficulty(p["difficulty"], snap.difficulty),
          checkpointVersion: asString(p["checkpoint_version"], snap.checkpointVersion),
          remainingSeconds: asNumber(p["remaining_seconds"], snap.remainingSeconds),
          tokensConsumed: asNumber(p["tokens_consumed"], snap.tokensConsumed),
          fencingToken: asNumber(p["fencing_token"], snap.fencingToken),
          isCompleted: Boolean(p["is_completed"]),
        });
        break;
      }

      case "stt.partial_transcript": {
        this.dispatch({
          type: "PARTIAL_TRANSCRIPT_RECEIVED",
          seq: frame.seq,
          partialTranscript: asString(p["partial_transcript"], ""),
          incrementAudioChunkCount: true,
        });
        break;
      }

      case "interviewer.thinking": {
        this.dispatch({
          type: "SERVER_THINKING",
          seq: frame.seq,
          stage: asString(p["stage"], "EVALUATING_AND_ROUTING"),
        });
        break;
      }

      case "turn.evaluation_ack": {
        this.dispatch({
          type: "TURN_EVALUATION_ACK",
          seq: frame.seq,
          turnNumber: asNumber(p["turn_number"], snap.turnNumber),
          routingAction: asRoutingAction(p["routing_action"]),
          nextDifficulty: asDifficulty(p["next_difficulty"], snap.difficulty),
          tokensConsumed: asNumber(p["tokens_consumed"], snap.tokensConsumed),
          checkpointVersion: asString(
            p["updated_checkpoint_version"],
            snap.checkpointVersion,
          ),
        });
        break;
      }

      case "interviewer.question": {
        const nextTurn = asNumber(p["turn_number"], snap.turnNumber + 1);
        const nextDiff = asDifficulty(p["difficulty"], snap.difficulty);
        this.audio.setAiSpeakingState(true);
        this.dispatch({
          type: "INTERVIEWER_QUESTION_RECEIVED",
          seq: frame.seq,
          turnNumber: nextTurn,
          questionText: asString(p["question_text"], ""),
          difficulty: nextDiff,
          routingAction: asRoutingAction(p["routing_action"]),
          checkpointVersion: asString(p["checkpoint_version"], snap.checkpointVersion),
        });
        this.telemetry.beginTurn(nextTurn, nextDiff, snap.accommodatedTextMode);
        break;
      }

      case "tts.audio_chunk": {
        const durationMs = asNumber(p["duration_ms"], 40);
        this.jitterCushionMs = computeAdaptiveJitterCushionMs(durationMs * 0.2);
        const audioBase64 = asString(p["audio_base64"], "");
        const samples = decodePcmStubFromBase64(audioBase64, durationMs);
        this.audio.enqueueTtsChunk(samples, durationMs);
        this.audio.setAiSpeakingState(true);
        const current = this.store.getSnapshot();
        if (
          current.state === "READY" ||
          current.state === "INTERVIEWER_THINKING" ||
          current.state === "RECONNECTING"
        ) {
          this.dispatch({ type: "TTS_AUDIO_STARTED", seq: frame.seq });
        }
        break;
      }

      case "tts.flush_interrupt": {
        this.audio.flushPlayback();
        this.audio.setAiSpeakingState(false);
        this.dispatch({
          type: "TTS_FLUSH_ACKED",
          seq: frame.seq,
          interruptedAtMs: asNumber(
            p["interrupted_at_ms"],
            snap.lastInterruptedPlaybackMs ?? 0,
          ),
        });
        break;
      }

      case "integrity.warning": {
        this.dispatch({
          type: "INTEGRITY_WARNING_RECEIVED",
          seq: frame.seq,
          turnNumber: asNumber(p["turn_number"], snap.turnNumber),
          reasonCode: asString(p["reason_code"], "INTEGRITY_ANOMALY"),
          severity: asString(p["severity"], "MEDIUM"),
        });
        break;
      }

      case "session.completed": {
        this.audio.stopCapture();
        this.audio.setAiSpeakingState(false);
        this.telemetry.detach();
        this.dispatch({
          type: "SESSION_COMPLETED",
          seq: frame.seq,
          closingMessage: asString(p["closing_message"], ""),
          tokensConsumed: asNumber(p["tokens_consumed"], snap.tokensConsumed),
        });
        break;
      }

      case "error.problem": {
        const code = asString(p["error_code"], "WS_PROTOCOL_ERROR");
        const detail = asString(p["detail"], "Interview session encountered a transport error.");
        if (snap.state !== "COMPLETED" && snap.state !== "TERMINATED_ERROR") {
          this.audio.stopCapture();
          this.telemetry.detach();
          this.dispatch({ type: "FATAL_ERROR", errorCode: code, errorDetail: detail });
        }
        break;
      }

      default:
        break;
    }
  }

  public async dispose(): Promise<void> {
    this.audio.stopCapture();
    this.telemetry.detach();
    for (const unsub of this.unsubscribers) {
      unsub();
    }
    this.unsubscribers.length = 0;
    await this.ws.disconnect();
  }

  private bindInfrastructureSubscriptions(): void {
    this.unsubscribers.push(
      this.ws.onFrame((frame) => {
        this.handleServerFrame(frame);
      }),
    );

    this.unsubscribers.push(
      this.ws.onConnectionStatusChange((status) => {
        const snap = this.store.getSnapshot();
        if (
          status === "RECONNECTING" &&
          snap.state !== "IDLE" &&
          snap.state !== "AUTHENTICATING" &&
          snap.state !== "COMPLETED" &&
          snap.state !== "TERMINATED_ERROR"
        ) {
          this.dispatch({
            type: "SOCKET_DISCONNECTED",
            reason: "WebSocket connection interrupted; awaiting session.state_sync.",
          });
        } else if (
          status === "DISCONNECTED" &&
          snap.state === "RECONNECTING"
        ) {
          this.dispatch({
            type: "SOCKET_RECONNECT_EXHAUSTED",
            errorDetail: "Reconnection grace window exhausted.",
          });
        }
      }),
    );

    this.unsubscribers.push(
      this.telemetry.subscribeSpikes((alert: TelemetrySpikeAlert) => {
        const snap = this.store.getSnapshot();
        if (
          snap.state === "CANDIDATE_LISTENING" ||
          snap.state === "CANDIDATE_SPEAKING" ||
          snap.state === "INTERVIEWER_SPEAKING"
        ) {
          void this.ws.sendFrame({
            event_type: "telemetry.snapshot",
            payload: { ...alert.telemetry.wirePayload },
          });
        }
      }),
    );
  }

  private dispatch(event: InterviewPortalEvent): void {
    this.store.update((prev) => transitionInterviewFsm(prev, event));
  }
}

function decodePcmStubFromBase64(b64: string, durationMs: number): Float32Array {
  const sampleCount = Math.max(48, Math.round((durationMs / 1000) * 48_000));
  const out = new Float32Array(sampleCount);
  if (!b64) {
    return out;
  }
  // Deterministic lightweight envelope derived from base64 characters (zero Buffer dependency)
  for (let i = 0; i < out.length; i += 1) {
    const code = b64.charCodeAt(i % b64.length);
    out[i] = ((code % 64) - 32) / 256;
  }
  return out;
}
