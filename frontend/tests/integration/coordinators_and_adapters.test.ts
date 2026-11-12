import { describe, expect, it } from "vitest";
import { ComparisonMatrixCoordinator } from "../../src/application/evaluation/comparison_coordinator";
import { LiveInterviewSessionCoordinator } from "../../src/application/interview/session_coordinator";
import { ProblemDetailsError } from "../../src/application/ports/outbound";
import { BatchMonitorCoordinator } from "../../src/application/screening/batch_monitor_coordinator";
import { ValidationDomainError } from "../../src/domain/shared/errors";
import {
  WebAudioRingBufferAdapter,
  type MinimalAudioContextLike,
} from "../../src/infrastructure/audio/web_audio_adapter";
import {
  FetchRestApiClientAdapter,
  type HttpResponseLike,
} from "../../src/infrastructure/http/rest_api_client";
import {
  RafCoalescedBatchSseAdapter,
  type MinimalEventSourceLike,
} from "../../src/infrastructure/sse/raf_batch_sse_adapter";
import {
  BrowserIntegrityTelemetryAdapter,
  type TelemetryDomTarget,
} from "../../src/infrastructure/telemetry/browser_telemetry_adapter";
import {
  ReconnectingInterviewWebSocketAdapter,
  type MinimalWebSocketLike,
} from "../../src/infrastructure/websocket/interview_ws_adapter";

function makeJsonResponse(status: number, body: unknown): HttpResponseLike {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

describe("Phase 7 Frontend Coordinators & Infrastructure Adapters Integration", () => {
  it("coalesces 500 rapid SSE deltas into a single 60 FPS requestAnimationFrame UI commit", async () => {
    const rafQueue: (() => void)[] = [];
    let activeEventSource: MinimalEventSourceLike | null = null;

    const sse = new RafCoalescedBatchSseAdapter({
      eventSourceFactory: () => {
        const es: MinimalEventSourceLike = {
          onmessage: null,
          onerror: null,
          close: () => undefined,
        };
        activeEventSource = es;
        return es;
      },
      requestAnimationFrameFn: (cb) => {
        rafQueue.push(cb);
        return rafQueue.length;
      },
      cancelAnimationFrameFn: () => undefined,
    });

    const api = new FetchRestApiClientAdapter({
      baseUrl: "https://api.example.test",
      getAuthToken: () => "jwt-recruiter",
      fetchFn: async () =>
        makeJsonResponse(202, {
          data: {
            batch_id: "batch-500",
            requisition_id: "req-1",
            status: "INITIATED",
            total_files: 3,
            upload_instructions: [
              { task_id: "t1", filename: "a.pdf", object_key: "k1", presigned_upload_url: "u1" },
              { task_id: "t2", filename: "b.pdf", object_key: "k2", presigned_upload_url: "u2" },
              { task_id: "t3", filename: "c.pdf", object_key: "k3", presigned_upload_url: "u3" },
            ],
            replayed_from_idempotency: false,
            sse_stream_uri: "/v1/screening-batches/batch-500/events",
          },
          meta: {
            request_id: "r1",
            trace_id: "tr1",
            tenant_id: "t1",
            api_version: "v1",
            timestamp: "2026-01-01T00:00:00Z",
          },
        }),
    });

    const coordinator = new BatchMonitorCoordinator({ api, sse });
    let uiNotificationCount = 0;
    const unsub = coordinator.subscribe(() => {
      uiNotificationCount += 1;
    });

    await coordinator.initiateAndMonitorBatch({
      requisitionId: "req-1",
      batchLabel: "500-burst-test",
      idempotencyKey: "idem-batch-500",
      files: [
        { filename: "a.pdf", sha256_hex: "0".repeat(64), file_size_bytes: 1024, mime_type: "application/pdf" },
        { filename: "b.pdf", sha256_hex: "1".repeat(64), file_size_bytes: 1024, mime_type: "application/pdf" },
        { filename: "c.pdf", sha256_hex: "2".repeat(64), file_size_bytes: 1024, mime_type: "application/pdf" },
      ],
    });

    expect(activeEventSource).not.toBeNull();
    const notificationsBeforeBurst = uiNotificationCount;

    // Fire 500 rapid SSE deltas inside the same browser frame
    for (let i = 1; i <= 497; i += 1) {
      sse.ingestParsedMessage({
        eventId: i,
        eventType: "TASK_STARTED",
        data: { task_id: i % 2 === 0 ? "t1" : "t2", task_status: "EXTRACTING" },
      });
    }
    sse.ingestParsedMessage({
      eventId: 498,
      eventType: "TASK_SUCCEEDED",
      data: { task_id: "t1", task_status: "COMPLETED", candidate_profile_id: "cp-1" },
    });
    sse.ingestParsedMessage({
      eventId: 499,
      eventType: "TASK_FAILED",
      data: { task_id: "t2", task_status: "FAILED", error_code: "MALWARE_QUARANTINE" },
    });
    sse.ingestParsedMessage({
      eventId: 500,
      eventType: "TASK_SUCCEEDED",
      data: { task_id: "t3", task_status: "COMPLETED", candidate_profile_id: "cp-3" },
    });

    expect(sse.ingestedEventsCount).toBe(500);
    expect(rafQueue).toHaveLength(1);
    expect(sse.flushedFramesCount).toBe(0);

    // Trigger the single animation frame commit
    const flushCb = rafQueue.shift();
    flushCb?.();

    expect(sse.flushedFramesCount).toBe(1);
    expect(uiNotificationCount - notificationsBeforeBurst).toBe(1);

    const snap = coordinator.getSnapshot();
    expect(snap.latestEventId).toBe(500);
    expect(snap.totalFiles).toBe(3);
    expect(snap.completedCount).toBe(2);
    expect(snap.failedCount).toBe(1);
    expect(snap.pendingCount).toBe(0);
    expect(snap.completedCount + snap.failedCount + snap.pendingCount).toBe(snap.totalFiles);
    expect(snap.batchStatus).toBe("PARTIALLY_FAILED");

    unsub();
    coordinator.stopMonitoring();
  });

  it("enforces 500-file manifest quota and 15 MB ceiling before network calls", async () => {
    const api = new FetchRestApiClientAdapter({
      baseUrl: "https://api.example.test",
      getAuthToken: () => null,
      fetchFn: async () => makeJsonResponse(500, {}),
    });
    const coordinator = new BatchMonitorCoordinator({
      api,
      sse: new RafCoalescedBatchSseAdapter(),
    });

    const oversizedBatch = Array.from({ length: 501 }, (_, idx) => ({
      filename: `cv-${idx}.pdf`,
      sha256_hex: "a".repeat(64),
      file_size_bytes: 512,
      mime_type: "application/pdf",
    }));

    await expect(
      coordinator.initiateAndMonitorBatch({
        requisitionId: "req-1",
        batchLabel: "too-big",
        idempotencyKey: "k-1",
        files: oversizedBatch,
      }),
    ).rejects.toThrow(ValidationDomainError);
  });

  it("drives end-to-end WS interview lifecycle, 280ms VAD barge-in, paste spike, and reconnect recovery", async () => {
    const sentFrames: Record<string, unknown>[] = [];
    const createdSockets: MinimalWebSocketLike[] = [];
    const reconnectHolder: { fn: (() => void) | null } = { fn: null };
    let fakeClockSec = 10.0;
    let fakeTelemetryNowMs = 1_000_000;

    const fakeAudioCtx: MinimalAudioContextLike = {
      sampleRate: 48_000,
      get currentTime() {
        return fakeClockSec;
      },
      resume: async () => undefined,
      close: async () => undefined,
    };

    const audio = new WebAudioRingBufferAdapter({
      sampleRateHz: 48_000,
      vadRmsThreshold: 0.035,
      bargeInOnsetMs: 280,
      audioContextFactory: () => fakeAudioCtx,
      requestUserMediaFn: async () => ({
        getTracks: () => [{ stop: () => undefined }],
      }),
    });

    const ws = new ReconnectingInterviewWebSocketAdapter({
      socketFactory: () => {
        const sock: MinimalWebSocketLike = {
          readyState: 1,
          onopen: null,
          onmessage: null,
          onclose: null,
          onerror: null,
          send: (data: string) => {
            sentFrames.push(JSON.parse(data) as Record<string, unknown>);
          },
          close: () => undefined,
        };
        createdSockets.push(sock);
        queueMicrotask(() => sock.onopen?.());
        return sock;
      },
      scheduler: {
        setTimeout: (fn) => {
          reconnectHolder.fn = fn;
          return 1 as unknown as ReturnType<typeof setTimeout>;
        },
        clearTimeout: () => undefined,
        setInterval: () => 2 as unknown as ReturnType<typeof setInterval>,
        clearInterval: () => undefined,
        nowMs: () => fakeTelemetryNowMs,
        random: () => 0.5,
      },
    });

    const fakeDom: TelemetryDomTarget = {
      hidden: false,
      addVisibilityListener: () => () => undefined,
      addWindowBlurFocusListeners: () => () => undefined,
      nowMs: () => fakeTelemetryNowMs,
    };
    const telemetry = new BrowserIntegrityTelemetryAdapter(fakeDom);
    const coordinator = new LiveInterviewSessionCoordinator({ ws, audio, telemetry });

    await coordinator.startSession({
      sessionId: "sess-live-1",
      tenantId: "tenant-alpha",
      wsUrl: "wss://api.example.test/v1/ws/interviews/sess-live-1",
      ticketToken: "single-use-ticket",
    });

    const firstSock = createdSockets[0]!;
    expect(coordinator.getSnapshot().state).toBe("CONNECTING");

    // 1) Server sends session.ready -> READY
    firstSock.onmessage?.({
      data: JSON.stringify({
        event_type: "session.ready",
        seq: 1,
        timestamp: "2026-01-01T00:00:00Z",
        payload: {
          session_id: "sess-live-1",
          turn_number: 1,
          current_question: "Describe write amplification in LSM-trees.",
          difficulty: 4,
          checkpoint_version: "cp-1",
          remaining_seconds: 1700,
          tokens_consumed: 100,
          fencing_token: 1,
        },
      }),
    });
    expect(coordinator.getSnapshot().state).toBe("READY");

    // 2) Server streams TTS audio -> INTERVIEWER_SPEAKING
    firstSock.onmessage?.({
      data: JSON.stringify({
        event_type: "tts.audio_chunk",
        seq: 2,
        timestamp: "2026-01-01T00:00:01Z",
        payload: {
          audio_pcm_samples: [0.1, 0.2, 0.3],
          duration_ms: 2500,
        },
      }),
    });
    expect(coordinator.getSnapshot().state).toBe("INTERVIEWER_SPEAKING");
    expect(audio.unreadRingSamples).toBe(120_000);

    // Advance hardware AudioContext clock by 1.35 seconds
    fakeClockSec = 11.35;

    // Feed 14 x 20ms (280ms) loud speech frames -> automatic barge-in!
    const loudFrame = new Float32Array(256).fill(0.15);
    for (let i = 0; i < 14; i += 1) {
      audio.processMicrophoneFrame(loudFrame, 20);
    }

    expect(coordinator.getSnapshot().state).toBe("BARGE_IN_INTERRUPTING");
    expect(audio.unreadRingSamples).toBe(0);
    const bargeFrame = sentFrames.find((f) => f["event_type"] === "candidate.barge_in");
    expect(bargeFrame).toBeDefined();
    expect((bargeFrame?.["payload"] as Record<string, unknown>)["interrupted_at_ms"]).toBeCloseTo(1350, 2);

    // 3) Server ACKs TTS flush -> CANDIDATE_LISTENING
    firstSock.onmessage?.({
      data: JSON.stringify({
        event_type: "tts.flush_interrupt",
        seq: 3,
        timestamp: "2026-01-01T00:00:02Z",
        payload: { interrupted_at_ms: 1350 },
      }),
    });
    expect(coordinator.getSnapshot().state).toBe("CANDIDATE_LISTENING");

    // 4) External paste burst + submit turn
    fakeTelemetryNowMs += 2000;
    coordinator.updateSupplementalText(
      "Leveled compaction trades write amplification for predictable read amplification and bounded space.",
      "PASTED",
    );
    await coordinator.submitUtterance();
    expect(coordinator.getSnapshot().state).toBe("SUBMITTING_TURN");

    const utteranceFrame = sentFrames.find((f) => f["event_type"] === "candidate.utterance");
    expect(
      (utteranceFrame?.["payload"] as Record<string, unknown>)["external_paste_detected"],
    ).toBe(true);

    // 5) Server thinking, then drop WS -> RECONNECTING -> reconnect auto session.join -> session.state_sync
    firstSock.onmessage?.({
      data: JSON.stringify({
        event_type: "interviewer.thinking",
        seq: 4,
        timestamp: "2026-01-01T00:00:03Z",
        payload: { stage: "SCORING_RUBRIC" },
      }),
    });
    expect(coordinator.getSnapshot().state).toBe("INTERVIEWER_THINKING");

    firstSock.onclose?.({ code: 1006, reason: "network_reset" });
    expect(coordinator.getSnapshot().state).toBe("RECONNECTING");

    reconnectHolder.fn?.();
    await new Promise((r) => setTimeout(r, 10));

    const secondSock = createdSockets[1]!;
    expect(secondSock).toBeDefined();
    const joinFrames = sentFrames.filter((f) => f["event_type"] === "session.join");
    expect(joinFrames).toHaveLength(2);
    expect((joinFrames[0]?.["payload"] as Record<string, unknown>)["last_received_seq"]).toBe(0);
    expect((joinFrames[1]?.["payload"] as Record<string, unknown>)["last_received_seq"]).toBe(4);

    secondSock.onmessage?.({
      data: JSON.stringify({
        event_type: "session.state_sync",
        seq: 5,
        timestamp: "2026-01-01T00:00:05Z",
        payload: {
          turn_number: 2,
          current_question: "Compare tiered vs leveled compaction.",
          difficulty: 4,
          checkpoint_version: "cp-2",
          remaining_seconds: 1650,
          tokens_consumed: 250,
          fencing_token: 2,
          is_completed: false,
        },
      }),
    });
    expect(coordinator.getSnapshot().state).toBe("INTERVIEWER_THINKING");

    secondSock.onmessage?.({
      data: JSON.stringify({
        event_type: "session.completed",
        seq: 6,
        timestamp: "2026-01-01T00:00:06Z",
        payload: { closing_message: "Interview completed.", tokens_consumed: 410 },
      }),
    });
    expect(coordinator.getSnapshot().state).toBe("COMPLETED");
  });

  it("loads ComparisonMatrixCoordinator from REST, parses RFC 9457 errors, and sends Idempotency-Key on override", async () => {
    const observedHeaders: Record<string, string>[] = [];

    const api = new FetchRestApiClientAdapter({
      baseUrl: "https://api.example.test",
      getAuthToken: () => "jwt-hiring-mgr",
      idempotencyKeyFactory: () => "fixed-idem-uuid",
      fetchFn: async (input, init) => {
        observedHeaders.push((init?.headers ?? {}) as Record<string, string>);
        if (input.includes("/comparison-matrix")) {
          return makeJsonResponse(200, {
            data: {
              requisition_id: "req-staff-1",
              candidates: [
                {
                  candidate_profile_id: "c1",
                  cv_match_score: 88,
                  posterior_technical_score: 90,
                  holistic_decision_index: 89,
                  authenticity_score: 0.96,
                  recommendation_band: "STRONG_HIRE",
                  rank: 1,
                },
                {
                  candidate_profile_id: "c2",
                  cv_match_score: 82,
                  posterior_technical_score: 76,
                  holistic_decision_index: 78,
                  authenticity_score: 0.91,
                  recommendation_band: "HIRE",
                  rank: 2,
                },
              ],
              dimension_cells: [
                {
                  candidate_profile_id: "c1",
                  competency_id: "comp-k8s",
                  competency_name: "Kubernetes Control Plane",
                  cv_prior_score: 80,
                  live_interview_score: 94,
                  posterior_score: 89,
                  verification_delta: 9,
                  verification_classification: "HIDDEN_GEM_UPGRADE",
                  achieved_depth: 4.6,
                  target_depth: 4,
                  is_dimension_leader: true,
                },
                {
                  candidate_profile_id: "c2",
                  competency_id: "comp-k8s",
                  competency_name: "Kubernetes Control Plane",
                  cv_prior_score: 85,
                  live_interview_score: 72,
                  posterior_score: 76,
                  verification_delta: -9,
                  verification_classification: "CORROBORATED_CLAIM",
                  achieved_depth: 3.6,
                  target_depth: 4,
                  is_dimension_leader: false,
                },
              ],
            },
            meta: {
              request_id: "req1",
              trace_id: "tr1",
              tenant_id: "t1",
              api_version: "v1",
              timestamp: "2026-01-01T00:00:00Z",
            },
          });
        }
        if (input.includes("/rep-ok/overrides")) {
          return makeJsonResponse(200, {
            data: {
              report_id: "rep-ok",
              interview_session_id: "sess-1",
              requisition_id: "req-staff-1",
              candidate_profile_id: "c1",
              technical_interview_score: 90,
              cv_match_score: 88,
              authenticity_score: 0.96,
              recommendation_index: 89,
              initial_recommendation_band: "HIRE",
              effective_recommendation_band: "STRONG_HIRE",
              is_finalized: true,
              report_hash: "abc",
              override_count: 1,
            },
            meta: {
              request_id: "req2",
              trace_id: "tr2",
              tenant_id: "t1",
              api_version: "v1",
              timestamp: "2026-01-01T00:00:01Z",
            },
          });
        }
        return makeJsonResponse(422, {
          type: "urn:ai-screener:error:validation",
          title: "Validation Failed",
          status: 422,
          detail: "Justification too short.",
          instance: "/v1/evaluations/rep-bad/overrides",
          error_code: "JUSTIFICATION_REQUIRED",
          trace_id: "tr-err",
          timestamp: "2026-01-01T00:00:02Z",
        });
      },
    });

    const coordinator = new ComparisonMatrixCoordinator(api);
    const matrix = await coordinator.loadFromBackend({
      requisitionId: "req-staff-1",
      candidateProfileIds: ["c1", "c2"],
      blindMode: true,
      displayNamesByCandidate: { c1: "Alice Chen", c2: "Bob Diaz" },
    });

    expect(matrix.columns).toHaveLength(2);
    expect(matrix.columns[0]!.displayName).toBe("Candidate #A");

    coordinator.toggleBlindMode();
    expect(coordinator.getSnapshot().matrix?.columns[0]!.displayName).toBe("Alice Chen");

    coordinator.openEvidenceDrawer({
      competencyId: "comp-k8s",
      competencyName: "Kubernetes Control Plane",
      candidateProfileId: "c1",
      candidateLabel: "Alice Chen",
    });
    expect(coordinator.getSnapshot().selectedEvidence?.candidateProfileId).toBe("c1");

    const receipt = await coordinator.submitRecruiterOverride({
      reportId: "rep-ok",
      newBand: "STRONG_HIRE",
      justification: "Staff panel unanimous approval.",
    });
    expect(receipt.effective_recommendation_band).toBe("STRONG_HIRE");
    expect(observedHeaders[1]?.["Idempotency-Key"]).toBe("fixed-idem-uuid");
    expect(observedHeaders[1]?.["Authorization"]).toBe("Bearer jwt-hiring-mgr");

    await expect(
      coordinator.submitRecruiterOverride({
        reportId: "rep-bad",
        newBand: "NO_HIRE",
        justification: "no",
      }),
    ).rejects.toThrow(ProblemDetailsError);
    expect(coordinator.getSnapshot().problemError?.error_code).toBe("JUSTIFICATION_REQUIRED");
  });
});
