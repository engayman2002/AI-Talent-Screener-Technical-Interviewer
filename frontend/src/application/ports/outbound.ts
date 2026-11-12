/**
 * Frontend Application Outbound Driven Ports (Hexagonal / DIP Boundary).
 * Imports ONLY Frontend Domain types; strictly zero React, Next.js, DOM, or browser APIs.
 */

import type {
  BatchStatus,
  DifficultyLevel,
  RecommendationBand,
  RoutingAction,
  SessionStatus,
  TaskIngestionStatus,
  VerificationClassification,
} from "../../domain/shared/types";
import type {
  ComputedTurnTelemetry,
  TelemetryWirePayload,
} from "../../domain/interview/telemetry_math";

export interface FieldViolationWire {
  readonly field_path: string;
  readonly constraint: string;
  readonly reason: string;
}

export interface ProblemDetailsPayload {
  readonly type: string;
  readonly title: string;
  readonly status: number;
  readonly detail: string;
  readonly instance: string;
  readonly error_code: string;
  readonly trace_id: string;
  readonly timestamp: string;
  readonly errors?: readonly FieldViolationWire[];
  readonly metadata?: Readonly<Record<string, unknown>>;
  readonly retry_after_seconds?: number;
}

export class ProblemDetailsError extends Error {
  public readonly problem: ProblemDetailsPayload;

  public constructor(problem: ProblemDetailsPayload) {
    super(`[${problem.error_code}] (${problem.status}) ${problem.detail}`);
    this.name = "ProblemDetailsError";
    this.problem = problem;
  }
}

export interface ApiEnvelopeMeta {
  readonly request_id: string;
  readonly trace_id: string;
  readonly tenant_id: string | null;
  readonly api_version: string;
  readonly timestamp: string;
}

export interface ApiSuccessEnvelope<T> {
  readonly data: T;
  readonly meta: ApiEnvelopeMeta;
}

export interface RestRequestOptions {
  readonly method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  readonly path: string;
  readonly body?: Readonly<Record<string, unknown>>;
  readonly headers?: Readonly<Record<string, string>>;
  readonly idempotencyKey?: string;
}

export interface BatchFileManifestWire {
  readonly filename: string;
  readonly sha256_hex: string;
  readonly file_size_bytes: number;
  readonly mime_type: string;
}

export interface InitiateBatchRequestWire {
  readonly requisition_id: string;
  readonly batch_label: string;
  readonly files: readonly BatchFileManifestWire[];
  readonly idempotency_key: string;
  readonly correlation_id?: string;
}

export interface PresignedUploadSlotWire {
  readonly task_id: string;
  readonly filename: string;
  readonly object_key: string;
  readonly presigned_upload_url: string;
}

export interface InitiateBatchResponseWire {
  readonly batch_id: string;
  readonly requisition_id: string;
  readonly status: BatchStatus;
  readonly total_files: number;
  readonly upload_instructions: readonly PresignedUploadSlotWire[];
  readonly replayed_from_idempotency: boolean;
  readonly sse_stream_uri?: string;
}

export interface TaskStatusReadWire {
  readonly task_id: string;
  readonly filename: string;
  readonly status: TaskIngestionStatus;
  readonly candidate_profile_id: string | null;
  readonly error_code: string | null;
}

export interface ScreeningBatchStatusWire {
  readonly batch_id: string;
  readonly requisition_id: string;
  readonly batch_label: string;
  readonly status: BatchStatus;
  readonly total_files: number;
  readonly completed_count: number;
  readonly failed_count: number;
  readonly pending_count: number;
  readonly tasks: readonly TaskStatusReadWire[];
}

export interface BackendComparisonCandidateSummaryWire {
  readonly candidate_profile_id: string;
  readonly cv_match_score: number;
  readonly posterior_technical_score: number;
  readonly holistic_decision_index: number;
  readonly authenticity_score: number;
  readonly recommendation_band: RecommendationBand;
  readonly rank: number;
}

export interface BackendComparisonCellWire {
  readonly candidate_profile_id: string;
  readonly competency_id: string;
  readonly competency_name: string;
  readonly cv_prior_score: number;
  readonly live_interview_score: number;
  readonly posterior_score: number;
  readonly verification_delta: number;
  readonly verification_classification: VerificationClassification;
  readonly achieved_depth: number;
  readonly target_depth: DifficultyLevel;
  readonly is_dimension_leader: boolean;
}

export interface BackendComparisonMatrixWire {
  readonly requisition_id: string;
  readonly candidates: readonly BackendComparisonCandidateSummaryWire[];
  readonly dimension_cells: readonly BackendComparisonCellWire[];
}

export interface RecruiterOverrideRequestWire {
  readonly new_band: RecommendationBand;
  readonly justification: string;
  readonly actor_id?: string;
}

export interface EvaluationReportResponseWire {
  readonly report_id: string;
  readonly interview_session_id: string;
  readonly requisition_id: string;
  readonly candidate_profile_id: string;
  readonly technical_interview_score: number;
  readonly cv_match_score: number;
  readonly authenticity_score: number;
  readonly recommendation_index: number;
  readonly initial_recommendation_band: RecommendationBand;
  readonly effective_recommendation_band: RecommendationBand;
  readonly is_finalized: boolean;
  readonly report_hash: string | null;
  readonly override_count: number;
}

export interface RestApiClientPort {
  request<T>(options: RestRequestOptions): Promise<ApiSuccessEnvelope<T>>;
  initiateScreeningBatch(
    payload: InitiateBatchRequestWire,
    idempotencyKey?: string,
  ): Promise<InitiateBatchResponseWire>;
  getScreeningBatchStatus(batchId: string): Promise<ScreeningBatchStatusWire>;
  fetchComparisonMatrix(
    requisitionId: string,
    candidateProfileIds: readonly string[],
  ): Promise<BackendComparisonMatrixWire>;
  recordRecruiterOverride(
    reportId: string,
    payload: RecruiterOverrideRequestWire,
    idempotencyKey?: string,
  ): Promise<EvaluationReportResponseWire>;
}

// ---------------------------------------------------------------------------
// Full-Duplex Interview WebSocket Port (aligned with WebSocketInterviewGateway)
// ---------------------------------------------------------------------------

export type WsConnectionStatus = "DISCONNECTED" | "CONNECTING" | "CONNECTED" | "RECONNECTING";

export interface ClientWsFrame {
  readonly event_type:
    | "session.join"
    | "audio.chunk"
    | "candidate.barge_in"
    | "candidate.utterance"
    | "telemetry.snapshot"
    | "session.terminate"
    | "ping";
  readonly payload: Readonly<Record<string, unknown>>;
}

export interface ServerWsFrameEnvelope {
  readonly event_type: string;
  readonly session_id: string;
  readonly seq: number;
  readonly turn_number: number;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly trace_id: string;
  readonly timestamp: string;
}

export interface WsConnectOptions {
  readonly url: string;
  readonly sessionId: string;
  readonly authToken: string;
  readonly lastServerSeq?: number;
}

export interface InterviewWebSocketPort {
  connect(options: WsConnectOptions): Promise<void>;
  disconnect(): Promise<void>;
  sendFrame(frame: ClientWsFrame): Promise<void>;
  onFrame(handler: (frame: ServerWsFrameEnvelope) => void): () => void;
  onConnectionStatusChange(handler: (status: WsConnectionStatus) => void): () => void;
  getLastServerSeq(): number;
}

// ---------------------------------------------------------------------------
// ADR-011 Batch SSE Stream Port
// ---------------------------------------------------------------------------

export interface SseTaskDelta {
  readonly taskId: string;
  readonly filename?: string;
  readonly status: TaskIngestionStatus;
  readonly errorCode?: string | null;
  readonly candidateProfileId?: string | null;
  readonly eventId: number;
  readonly eventType: string;
}

export interface CoalescedBatchProgressSnapshot {
  readonly batchId: string;
  readonly latestEventId: number;
  readonly batchStatus?: BatchStatus;
  readonly taskUpdates: readonly SseTaskDelta[];
}

export interface BatchSseSubscribeOptions {
  readonly batchId: string;
  readonly streamUrl: string;
  readonly lastEventId?: number;
}

export interface BatchSseStreamPort {
  subscribe(
    options: BatchSseSubscribeOptions,
    onProgress: (snapshot: CoalescedBatchProgressSnapshot) => void,
    onError: (err: Error) => void,
  ): () => void;
}

// ---------------------------------------------------------------------------
// Web Audio Pipeline Port
// ---------------------------------------------------------------------------

export interface AudioHardwareStatus {
  readonly microphoneGranted: boolean;
  readonly sampleRateHz: number;
  readonly echoCancellationEnabled: boolean;
}

export interface AudioCaptureCallbacks {
  readonly onFrameCaptured: (samples: Float32Array, rmsEnergy: number) => void;
  readonly onVadSpeechStart: () => void;
  readonly onVadSpeechEnd: () => void;
  readonly onBargeInDetected: (playedMs: number) => void;
}

export interface AudioEnginePort {
  requestMicrophone(): Promise<AudioHardwareStatus>;
  startCapture(callbacks: AudioCaptureCallbacks): void;
  stopCapture(): void;
  enqueueTtsChunk(pcmSamples: Float32Array, durationMs: number): void;
  /** Flush unplayed TTS buffer immediately and return exact ms played before cut. */
  flushPlayback(): number;
  getCurrentPlaybackOffsetMs(): number;
  setAiSpeakingState(speaking: boolean): void;
}

// ---------------------------------------------------------------------------
// Integrity Telemetry Collector Port
// ---------------------------------------------------------------------------

export interface TelemetrySpikeAlert {
  readonly trigger: "BLUR_SPIKE" | "PASTE_BURST" | "KEYSTROKE_MACRO";
  readonly telemetry: ComputedTurnTelemetry;
}

export interface IntegrityTelemetryCollectorPort {
  attach(sessionId: string): void;
  detach(): void;
  beginTurn(turnNumber: number, difficulty: DifficultyLevel, accommodatedTextMode: boolean): void;
  recordTextInput(deltaChars: number, source: "TYPED" | "PASTED"): void;
  recordSpeechStats(ttftMs: number, wpm: number, pauseEntropy: number): void;
  snapshotCurrentTurn(): ComputedTurnTelemetry;
  getLatestWirePayload(): TelemetryWirePayload;
  subscribeSpikes(handler: (alert: TelemetrySpikeAlert) => void): () => void;
}

// Re-export RoutingAction and SessionStatus cleanly for coordinator convenience
export type { RoutingAction, SessionStatus };
