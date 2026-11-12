/**
 * 13-State Candidate Interview Portal Finite State Machine (ADR-010).
 * Pure deterministic transition function + invariant guards.
 */

import { InvalidFrontendStateTransitionError } from "../shared/errors";
import type { DifficultyLevel, RoutingAction } from "../shared/types";

export const INTERVIEW_FSM_STATES = [
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
] as const;

export type InterviewFsmState = (typeof INTERVIEW_FSM_STATES)[number];

export type ActiveResumableState =
  | "READY"
  | "INTERVIEWER_SPEAKING"
  | "CANDIDATE_LISTENING"
  | "CANDIDATE_SPEAKING"
  | "BARGE_IN_INTERRUPTING"
  | "SUBMITTING_TURN"
  | "INTERVIEWER_THINKING";

export interface TranscriptLedgerEntry {
  readonly turnNumber: number;
  readonly role: "INTERVIEWER" | "CANDIDATE";
  readonly text: string;
  readonly difficulty: DifficultyLevel;
  readonly truncatedAtMs: number | null;
}

export interface IntegrityWarningItem {
  readonly turnNumber: number;
  readonly reasonCode: string;
  readonly severity: string;
}

export interface InterviewPortalSnapshot {
  readonly state: InterviewFsmState;
  readonly previousActiveState: ActiveResumableState | null;
  readonly sessionId: string | null;
  readonly tenantId: string | null;
  readonly turnNumber: number;
  readonly difficulty: DifficultyLevel;
  readonly checkpointVersion: string;
  readonly activeQuestion: string;
  readonly partialTranscript: string;
  readonly draftSupplementalText: string;
  readonly audioChunksSentInTurn: number;
  readonly remainingSeconds: number;
  readonly tokensConsumed: number;
  readonly fencingToken: number;
  readonly lastServerSeq: number;
  readonly reconnectAttempts: number;
  readonly lastInterruptedPlaybackMs: number | null;
  readonly lastRoutingAction: RoutingAction | null;
  readonly thinkingStage: string | null;
  readonly errorCode: string | null;
  readonly errorDetail: string | null;
  readonly accommodatedTextMode: boolean;
  readonly transcriptLedger: readonly TranscriptLedgerEntry[];
  readonly integrityWarnings: readonly IntegrityWarningItem[];
}

export function createInitialInterviewSnapshot(
  accommodatedTextMode = false,
): InterviewPortalSnapshot {
  return {
    state: "IDLE",
    previousActiveState: null,
    sessionId: null,
    tenantId: null,
    turnNumber: 1,
    difficulty: 3,
    checkpointVersion: "",
    activeQuestion: "",
    partialTranscript: "",
    draftSupplementalText: "",
    audioChunksSentInTurn: 0,
    remainingSeconds: 1800,
    tokensConsumed: 0,
    fencingToken: 0,
    lastServerSeq: 0,
    reconnectAttempts: 0,
    lastInterruptedPlaybackMs: null,
    lastRoutingAction: null,
    thinkingStage: null,
    errorCode: null,
    errorDetail: null,
    accommodatedTextMode,
    transcriptLedger: [],
    integrityWarnings: [],
  };
}

export type InterviewPortalEvent =
  | { readonly type: "START_HANDSHAKE"; readonly sessionId: string; readonly tenantId?: string }
  | { readonly type: "HANDSHAKE_SUCCEEDED" }
  | { readonly type: "HANDSHAKE_FAILED"; readonly errorCode: string; readonly errorDetail: string }
  | {
      readonly type: "SOCKET_SESSION_READY";
      readonly seq: number;
      readonly turnNumber: number;
      readonly activeQuestion: string;
      readonly difficulty: DifficultyLevel;
      readonly checkpointVersion: string;
      readonly remainingSeconds: number;
      readonly tokensConsumed: number;
      readonly fencingToken: number;
    }
  | { readonly type: "TTS_AUDIO_STARTED"; readonly seq?: number }
  | {
      readonly type: "INTERVIEWER_QUESTION_RECEIVED";
      readonly seq: number;
      readonly turnNumber: number;
      readonly questionText: string;
      readonly difficulty: DifficultyLevel;
      readonly routingAction: RoutingAction;
      readonly checkpointVersion: string;
    }
  | { readonly type: "TURN_READY_FOR_CANDIDATE" }
  | {
      readonly type: "PARTIAL_TRANSCRIPT_RECEIVED";
      readonly seq: number;
      readonly partialTranscript: string;
      readonly incrementAudioChunkCount?: boolean;
    }
  | { readonly type: "UPDATE_SUPPLEMENTAL_TEXT"; readonly text: string }
  | { readonly type: "CANDIDATE_SPEECH_DETECTED" }
  | { readonly type: "CANDIDATE_SILENCE_PAUSED" }
  | { readonly type: "BARGE_IN_TRIGGERED"; readonly playedMs: number }
  | { readonly type: "TTS_FLUSH_ACKED"; readonly interruptedAtMs?: number; readonly seq?: number }
  | { readonly type: "SUBMIT_TURN"; readonly utteranceOverride?: string }
  | { readonly type: "SERVER_THINKING"; readonly seq: number; readonly stage: string }
  | {
      readonly type: "TURN_EVALUATION_ACK";
      readonly seq: number;
      readonly turnNumber: number;
      readonly routingAction: RoutingAction;
      readonly nextDifficulty: DifficultyLevel;
      readonly tokensConsumed: number;
      readonly checkpointVersion: string;
    }
  | {
      readonly type: "INTEGRITY_WARNING_RECEIVED";
      readonly seq: number;
      readonly turnNumber: number;
      readonly reasonCode: string;
      readonly severity: string;
    }
  | { readonly type: "SOCKET_DISCONNECTED"; readonly reason?: string }
  | {
      readonly type: "SESSION_STATE_SYNC";
      readonly seq: number;
      readonly turnNumber: number;
      readonly activeQuestion: string;
      readonly difficulty: DifficultyLevel;
      readonly checkpointVersion: string;
      readonly remainingSeconds: number;
      readonly tokensConsumed: number;
      readonly fencingToken: number;
      readonly isCompleted: boolean;
    }
  | { readonly type: "SOCKET_RECONNECT_EXHAUSTED"; readonly errorDetail: string }
  | {
      readonly type: "SESSION_COMPLETED";
      readonly seq: number;
      readonly closingMessage?: string;
      readonly tokensConsumed?: number;
    }
  | { readonly type: "FATAL_ERROR"; readonly errorCode: string; readonly errorDetail: string }
  | { readonly type: "SET_ACCOMMODATED_TEXT_MODE"; readonly enabled: boolean };

const ALLOWED_TRANSITIONS: Readonly<Record<InterviewFsmState, ReadonlySet<InterviewFsmState>>> = {
  IDLE: new Set(["AUTHENTICATING", "TERMINATED_ERROR"]),
  AUTHENTICATING: new Set(["CONNECTING", "TERMINATED_ERROR"]),
  CONNECTING: new Set(["READY", "RECONNECTING", "COMPLETED", "TERMINATED_ERROR"]),
  READY: new Set(["INTERVIEWER_SPEAKING", "CANDIDATE_LISTENING", "RECONNECTING", "TERMINATED_ERROR"]),
  INTERVIEWER_SPEAKING: new Set([
    "CANDIDATE_LISTENING",
    "BARGE_IN_INTERRUPTING",
    "RECONNECTING",
    "COMPLETED",
    "TERMINATED_ERROR",
  ]),
  CANDIDATE_LISTENING: new Set([
    "CANDIDATE_SPEAKING",
    "SUBMITTING_TURN",
    "INTERVIEWER_SPEAKING",
    "RECONNECTING",
    "COMPLETED",
    "TERMINATED_ERROR",
  ]),
  CANDIDATE_SPEAKING: new Set([
    "CANDIDATE_LISTENING",
    "SUBMITTING_TURN",
    "RECONNECTING",
    "TERMINATED_ERROR",
  ]),
  BARGE_IN_INTERRUPTING: new Set(["CANDIDATE_LISTENING", "RECONNECTING", "TERMINATED_ERROR"]),
  SUBMITTING_TURN: new Set([
    "INTERVIEWER_THINKING",
    "RECONNECTING",
    "COMPLETED",
    "TERMINATED_ERROR",
  ]),
  INTERVIEWER_THINKING: new Set([
    "INTERVIEWER_SPEAKING",
    "CANDIDATE_LISTENING",
    "RECONNECTING",
    "COMPLETED",
    "TERMINATED_ERROR",
  ]),
  RECONNECTING: new Set([
    "READY",
    "INTERVIEWER_SPEAKING",
    "CANDIDATE_LISTENING",
    "INTERVIEWER_THINKING",
    "COMPLETED",
    "TERMINATED_ERROR",
  ]),
  COMPLETED: new Set(),
  TERMINATED_ERROR: new Set(),
};

function isActiveResumable(state: InterviewFsmState): state is ActiveResumableState {
  return (
    state === "READY" ||
    state === "INTERVIEWER_SPEAKING" ||
    state === "CANDIDATE_LISTENING" ||
    state === "CANDIDATE_SPEAKING" ||
    state === "BARGE_IN_INTERRUPTING" ||
    state === "SUBMITTING_TURN" ||
    state === "INTERVIEWER_THINKING"
  );
}

function assertTransitionAllowed(
  from: InterviewFsmState,
  to: InterviewFsmState,
  eventType: string,
): void {
  if (from === to) {
    return;
  }
  const allowed = ALLOWED_TRANSITIONS[from];
  if (!allowed.has(to)) {
    throw new InvalidFrontendStateTransitionError(
      `Illegal FSM transition ${from} -> ${to} on event ${eventType}.`,
      from,
      eventType,
    );
  }
}

export function transitionInterviewFsm(
  snapshot: InterviewPortalSnapshot,
  event: InterviewPortalEvent,
): InterviewPortalSnapshot {
  const cur = snapshot.state;

  switch (event.type) {
    case "SET_ACCOMMODATED_TEXT_MODE":
      return { ...snapshot, accommodatedTextMode: event.enabled };

    case "UPDATE_SUPPLEMENTAL_TEXT": {
      if (cur === "COMPLETED" || cur === "TERMINATED_ERROR") {
        throw new InvalidFrontendStateTransitionError(
          `Cannot edit supplemental text in terminal state ${cur}.`,
          cur,
          event.type,
        );
      }
      return { ...snapshot, draftSupplementalText: event.text };
    }

    case "PARTIAL_TRANSCRIPT_RECEIVED": {
      const seq = Math.max(snapshot.lastServerSeq, event.seq);
      return {
        ...snapshot,
        lastServerSeq: seq,
        partialTranscript: event.partialTranscript,
        audioChunksSentInTurn: event.incrementAudioChunkCount
          ? snapshot.audioChunksSentInTurn + 1
          : snapshot.audioChunksSentInTurn,
      };
    }

    case "INTEGRITY_WARNING_RECEIVED": {
      const warning: IntegrityWarningItem = {
        turnNumber: event.turnNumber,
        reasonCode: event.reasonCode,
        severity: event.severity,
      };
      return {
        ...snapshot,
        lastServerSeq: Math.max(snapshot.lastServerSeq, event.seq),
        integrityWarnings: [...snapshot.integrityWarnings, warning],
      };
    }

    case "START_HANDSHAKE": {
      if (!event.sessionId.trim()) {
        throw new InvalidFrontendStateTransitionError(
          "sessionId must be non-empty when starting handshake.",
          cur,
          event.type,
          "INVALID_SESSION_ID",
        );
      }
      assertTransitionAllowed(cur, "AUTHENTICATING", event.type);
      return {
        ...snapshot,
        state: "AUTHENTICATING",
        sessionId: event.sessionId.trim(),
        tenantId: event.tenantId ?? snapshot.tenantId,
        errorCode: null,
        errorDetail: null,
      };
    }

    case "HANDSHAKE_SUCCEEDED": {
      assertTransitionAllowed(cur, "CONNECTING", event.type);
      return { ...snapshot, state: "CONNECTING" };
    }

    case "HANDSHAKE_FAILED": {
      assertTransitionAllowed(cur, "TERMINATED_ERROR", event.type);
      return {
        ...snapshot,
        state: "TERMINATED_ERROR",
        errorCode: event.errorCode,
        errorDetail: event.errorDetail,
      };
    }

    case "SOCKET_SESSION_READY": {
      assertTransitionAllowed(cur, "READY", event.type);
      const nextLedger =
        event.activeQuestion.trim().length > 0
          ? [
              ...snapshot.transcriptLedger,
              {
                turnNumber: event.turnNumber,
                role: "INTERVIEWER" as const,
                text: event.activeQuestion,
                difficulty: event.difficulty,
                truncatedAtMs: null,
              },
            ]
          : snapshot.transcriptLedger;
      return {
        ...snapshot,
        state: "READY",
        previousActiveState: "READY",
        turnNumber: event.turnNumber,
        activeQuestion: event.activeQuestion,
        difficulty: event.difficulty,
        checkpointVersion: event.checkpointVersion,
        remainingSeconds: event.remainingSeconds,
        tokensConsumed: event.tokensConsumed,
        fencingToken: event.fencingToken,
        lastServerSeq: Math.max(snapshot.lastServerSeq, event.seq),
        reconnectAttempts: 0,
        transcriptLedger: nextLedger,
      };
    }

    case "TTS_AUDIO_STARTED": {
      assertTransitionAllowed(cur, "INTERVIEWER_SPEAKING", event.type);
      return {
        ...snapshot,
        state: "INTERVIEWER_SPEAKING",
        previousActiveState: "INTERVIEWER_SPEAKING",
        lastServerSeq:
          event.seq !== undefined
            ? Math.max(snapshot.lastServerSeq, event.seq)
            : snapshot.lastServerSeq,
      };
    }

    case "INTERVIEWER_QUESTION_RECEIVED": {
      assertTransitionAllowed(cur, "INTERVIEWER_SPEAKING", event.type);
      const entry: TranscriptLedgerEntry = {
        turnNumber: event.turnNumber,
        role: "INTERVIEWER",
        text: event.questionText,
        difficulty: event.difficulty,
        truncatedAtMs: null,
      };
      return {
        ...snapshot,
        state: "INTERVIEWER_SPEAKING",
        previousActiveState: "INTERVIEWER_SPEAKING",
        turnNumber: event.turnNumber,
        activeQuestion: event.questionText,
        difficulty: event.difficulty,
        lastRoutingAction: event.routingAction,
        checkpointVersion: event.checkpointVersion,
        thinkingStage: null,
        partialTranscript: "",
        draftSupplementalText: "",
        audioChunksSentInTurn: 0,
        lastInterruptedPlaybackMs: null,
        lastServerSeq: Math.max(snapshot.lastServerSeq, event.seq),
        transcriptLedger: [...snapshot.transcriptLedger, entry],
      };
    }

    case "TURN_READY_FOR_CANDIDATE": {
      assertTransitionAllowed(cur, "CANDIDATE_LISTENING", event.type);
      return {
        ...snapshot,
        state: "CANDIDATE_LISTENING",
        previousActiveState: "CANDIDATE_LISTENING",
      };
    }

    case "CANDIDATE_SPEECH_DETECTED": {
      assertTransitionAllowed(cur, "CANDIDATE_SPEAKING", event.type);
      return {
        ...snapshot,
        state: "CANDIDATE_SPEAKING",
        previousActiveState: "CANDIDATE_SPEAKING",
      };
    }

    case "CANDIDATE_SILENCE_PAUSED": {
      assertTransitionAllowed(cur, "CANDIDATE_LISTENING", event.type);
      return {
        ...snapshot,
        state: "CANDIDATE_LISTENING",
        previousActiveState: "CANDIDATE_LISTENING",
      };
    }

    case "BARGE_IN_TRIGGERED": {
      assertTransitionAllowed(cur, "BARGE_IN_INTERRUPTING", event.type);
      const clampedMs = Math.max(0, Math.round(event.playedMs));
      const updatedLedger = snapshot.transcriptLedger.map((item, idx) =>
        idx === snapshot.transcriptLedger.length - 1 && item.role === "INTERVIEWER"
          ? { ...item, truncatedAtMs: clampedMs }
          : item,
      );
      return {
        ...snapshot,
        state: "BARGE_IN_INTERRUPTING",
        previousActiveState: "BARGE_IN_INTERRUPTING",
        lastInterruptedPlaybackMs: clampedMs,
        transcriptLedger: updatedLedger,
      };
    }

    case "TTS_FLUSH_ACKED": {
      assertTransitionAllowed(cur, "CANDIDATE_LISTENING", event.type);
      const updatedLedger =
        event.interruptedAtMs !== undefined
          ? snapshot.transcriptLedger.map((item, idx) =>
              idx === snapshot.transcriptLedger.length - 1 && item.role === "INTERVIEWER"
                ? { ...item, truncatedAtMs: event.interruptedAtMs ?? item.truncatedAtMs }
                : item,
            )
          : snapshot.transcriptLedger;
      return {
        ...snapshot,
        state: "CANDIDATE_LISTENING",
        previousActiveState: "CANDIDATE_LISTENING",
        lastInterruptedPlaybackMs:
          event.interruptedAtMs ?? snapshot.lastInterruptedPlaybackMs,
        lastServerSeq:
          event.seq !== undefined
            ? Math.max(snapshot.lastServerSeq, event.seq)
            : snapshot.lastServerSeq,
        transcriptLedger: updatedLedger,
      };
    }

    case "SUBMIT_TURN": {
      assertTransitionAllowed(cur, "SUBMITTING_TURN", event.type);
      const combined = (
        event.utteranceOverride !== undefined
          ? event.utteranceOverride
          : `${snapshot.partialTranscript} ${snapshot.draftSupplementalText}`
      ).trim();
      if (!combined) {
        throw new InvalidFrontendStateTransitionError(
          "Cannot submit empty candidate utterance.",
          cur,
          event.type,
          "EMPTY_CANDIDATE_UTTERANCE",
        );
      }
      const entry: TranscriptLedgerEntry = {
        turnNumber: snapshot.turnNumber,
        role: "CANDIDATE",
        text: combined,
        difficulty: snapshot.difficulty,
        truncatedAtMs: null,
      };
      return {
        ...snapshot,
        state: "SUBMITTING_TURN",
        previousActiveState: "SUBMITTING_TURN",
        thinkingStage: "SUBMITTING",
        transcriptLedger: [...snapshot.transcriptLedger, entry],
      };
    }

    case "SERVER_THINKING": {
      assertTransitionAllowed(cur, "INTERVIEWER_THINKING", event.type);
      return {
        ...snapshot,
        state: "INTERVIEWER_THINKING",
        previousActiveState: "INTERVIEWER_THINKING",
        thinkingStage: event.stage,
        lastServerSeq: Math.max(snapshot.lastServerSeq, event.seq),
      };
    }

    case "TURN_EVALUATION_ACK": {
      assertTransitionAllowed(cur, "INTERVIEWER_THINKING", event.type);
      return {
        ...snapshot,
        state: "INTERVIEWER_THINKING",
        previousActiveState: "INTERVIEWER_THINKING",
        lastRoutingAction: event.routingAction,
        difficulty: event.nextDifficulty,
        tokensConsumed: event.tokensConsumed,
        checkpointVersion: event.checkpointVersion,
        lastServerSeq: Math.max(snapshot.lastServerSeq, event.seq),
      };
    }

    case "SOCKET_DISCONNECTED": {
      assertTransitionAllowed(cur, "RECONNECTING", event.type);
      const preserved = isActiveResumable(cur) ? cur : snapshot.previousActiveState;
      return {
        ...snapshot,
        state: "RECONNECTING",
        previousActiveState: preserved,
        reconnectAttempts: snapshot.reconnectAttempts + 1,
        errorDetail: event.reason ?? snapshot.errorDetail,
      };
    }

    case "SESSION_STATE_SYNC": {
      const target: InterviewFsmState = event.isCompleted
        ? "COMPLETED"
        : snapshot.previousActiveState === "INTERVIEWER_SPEAKING"
          ? "INTERVIEWER_SPEAKING"
          : snapshot.previousActiveState === "INTERVIEWER_THINKING"
            ? "INTERVIEWER_THINKING"
            : snapshot.activeQuestion || event.activeQuestion
              ? "CANDIDATE_LISTENING"
              : "READY";
      assertTransitionAllowed(cur, target, event.type);
      return {
        ...snapshot,
        state: target,
        previousActiveState: isActiveResumable(target) ? target : null,
        turnNumber: event.turnNumber,
        activeQuestion: event.activeQuestion,
        difficulty: event.difficulty,
        checkpointVersion: event.checkpointVersion,
        remainingSeconds: event.remainingSeconds,
        tokensConsumed: event.tokensConsumed,
        fencingToken: event.fencingToken,
        lastServerSeq: Math.max(snapshot.lastServerSeq, event.seq),
        reconnectAttempts: 0,
        errorDetail: null,
      };
    }

    case "SOCKET_RECONNECT_EXHAUSTED": {
      assertTransitionAllowed(cur, "TERMINATED_ERROR", event.type);
      return {
        ...snapshot,
        state: "TERMINATED_ERROR",
        errorCode: "WS_RECONNECT_GRACE_EXHAUSTED",
        errorDetail: event.errorDetail,
      };
    }

    case "SESSION_COMPLETED": {
      assertTransitionAllowed(cur, "COMPLETED", event.type);
      const nextLedger =
        event.closingMessage && event.closingMessage.trim().length > 0
          ? [
              ...snapshot.transcriptLedger,
              {
                turnNumber: snapshot.turnNumber,
                role: "INTERVIEWER" as const,
                text: event.closingMessage,
                difficulty: snapshot.difficulty,
                truncatedAtMs: null,
              },
            ]
          : snapshot.transcriptLedger;
      return {
        ...snapshot,
        state: "COMPLETED",
        previousActiveState: null,
        thinkingStage: null,
        tokensConsumed: event.tokensConsumed ?? snapshot.tokensConsumed,
        lastServerSeq: Math.max(snapshot.lastServerSeq, event.seq),
        transcriptLedger: nextLedger,
      };
    }

    case "FATAL_ERROR": {
      assertTransitionAllowed(cur, "TERMINATED_ERROR", event.type);
      return {
        ...snapshot,
        state: "TERMINATED_ERROR",
        errorCode: event.errorCode,
        errorDetail: event.errorDetail,
      };
    }
  }
}
