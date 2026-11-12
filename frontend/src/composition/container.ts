/**
 * Outermost Frontend Composition Root (ADR-010).
 * Wires concrete Browser & Network Infrastructure Adapters into Application Coordinators.
 */

import { ComparisonMatrixCoordinator } from "../application/evaluation/comparison_coordinator";
import { LiveInterviewSessionCoordinator } from "../application/interview/session_coordinator";
import type { RestApiClientPort } from "../application/ports/outbound";
import { BatchMonitorCoordinator } from "../application/screening/batch_monitor_coordinator";
import { WebAudioRingBufferAdapter } from "../infrastructure/audio/web_audio_adapter";
import { FetchRestApiClientAdapter } from "../infrastructure/http/rest_api_client";
import { RafCoalescedBatchSseAdapter } from "../infrastructure/sse/raf_batch_sse_adapter";
import { BrowserIntegrityTelemetryAdapter } from "../infrastructure/telemetry/browser_telemetry_adapter";
import { ReconnectingInterviewWebSocketAdapter } from "../infrastructure/websocket/interview_ws_adapter";

export interface FrontendServicesBundle {
  readonly restClient: RestApiClientPort;
  readonly interviewCoordinator: LiveInterviewSessionCoordinator;
  readonly batchCoordinator: BatchMonitorCoordinator;
  readonly comparisonCoordinator: ComparisonMatrixCoordinator;
}

export interface FrontendContainerConfig {
  readonly apiBaseUrl: string;
  readonly getAuthToken: () => string | null;
  readonly accommodatedTextMode?: boolean;
}

export function createFrontendServices(
  config: FrontendContainerConfig,
): FrontendServicesBundle {
  const restClient = new FetchRestApiClientAdapter({
    baseUrl: config.apiBaseUrl,
    getAuthToken: config.getAuthToken,
  });
  const ws = new ReconnectingInterviewWebSocketAdapter();
  const audio = new WebAudioRingBufferAdapter();
  const telemetry = new BrowserIntegrityTelemetryAdapter();
  const sse = new RafCoalescedBatchSseAdapter();

  return {
    restClient,
    interviewCoordinator: new LiveInterviewSessionCoordinator({
      ws,
      audio,
      telemetry,
      ...(config.accommodatedTextMode !== undefined
        ? { accommodatedTextMode: config.accommodatedTextMode }
        : {}),
    }),
    batchCoordinator: new BatchMonitorCoordinator({
      api: restClient,
      sse,
    }),
    comparisonCoordinator: new ComparisonMatrixCoordinator(restClient),
  };
}
