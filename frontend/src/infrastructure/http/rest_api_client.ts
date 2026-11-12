/**
 * REST API Client Adapter implementing RestApiClientPort (Frontend Infrastructure Layer).
 * Handles Bearer JWT headers, Idempotency-Key generation, ApiSuccessEnvelope unwrapping,
 * and RFC 9457 ProblemDetailsError translation.
 */

import {
  ProblemDetailsError,
  type ApiSuccessEnvelope,
  type BackendComparisonMatrixWire,
  type EvaluationReportResponseWire,
  type FieldViolationWire,
  type InitiateBatchRequestWire,
  type InitiateBatchResponseWire,
  type ProblemDetailsPayload,
  type RecruiterOverrideRequestWire,
  type RestApiClientPort,
  type RestRequestOptions,
  type ScreeningBatchStatusWire,
} from "../../application/ports/outbound";

export interface HttpResponseLike {
  readonly ok: boolean;
  readonly status: number;
  readonly headers?: { get(name: string): string | null };
  json(): Promise<unknown>;
  text(): Promise<string>;
}

export type FetchLike = (
  input: string,
  init: {
    method: string;
    headers: Record<string, string>;
    body?: string;
  },
) => Promise<HttpResponseLike>;

export interface RestClientConfig {
  readonly baseUrl: string;
  readonly getAuthToken: () => string | null;
  readonly fetchFn?: FetchLike;
  readonly idempotencyKeyFactory?: () => string;
}

function defaultIdempotencyKey(): string {
  if (typeof globalThis.crypto !== "undefined" && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeProblemPayload(
  raw: unknown,
  status: number,
  instancePath: string,
): ProblemDetailsPayload {
  if (isRecord(raw)) {
    const retryVal = raw["retry_after_seconds"];
    return {
      type: typeof raw["type"] === "string" ? raw["type"] : "urn:ai-screener:error:http",
      title: typeof raw["title"] === "string" ? raw["title"] : `HTTP ${status}`,
      status: typeof raw["status"] === "number" ? raw["status"] : status,
      detail:
        typeof raw["detail"] === "string"
          ? raw["detail"]
          : "Request failed with HTTP error status.",
      instance: typeof raw["instance"] === "string" ? raw["instance"] : instancePath,
      error_code:
        typeof raw["error_code"] === "string" ? raw["error_code"] : `HTTP_${status}`,
      trace_id: typeof raw["trace_id"] === "string" ? raw["trace_id"] : "unknown-trace",
      timestamp:
        typeof raw["timestamp"] === "string"
          ? raw["timestamp"]
          : new Date().toISOString(),
      ...(Array.isArray(raw["errors"]) ? { errors: raw["errors"] as readonly FieldViolationWire[] } : {}),
      ...(isRecord(raw["metadata"]) ? { metadata: raw["metadata"] } : {}),
      ...(typeof retryVal === "number" ? { retry_after_seconds: retryVal } : {}),
    };
  }
  return {
    type: "urn:ai-screener:error:http",
    title: `HTTP ${status}`,
    status,
    detail: `Unexpected non-JSON error response (${status}).`,
    instance: instancePath,
    error_code: `HTTP_${status}`,
    trace_id: "unknown-trace",
    timestamp: new Date().toISOString(),
  };
}

export class FetchRestApiClientAdapter implements RestApiClientPort {
  private readonly baseUrl: string;
  private readonly getAuthToken: () => string | null;
  private readonly fetchFn: FetchLike;
  private readonly generateKey: () => string;

  public constructor(config: RestClientConfig) {
    this.baseUrl = config.baseUrl.replace(/\/+$/, "");
    this.getAuthToken = config.getAuthToken;
    this.fetchFn =
      config.fetchFn ??
      ((input, init) => globalThis.fetch(input, init) as Promise<HttpResponseLike>);
    this.generateKey = config.idempotencyKeyFactory ?? defaultIdempotencyKey;
  }

  public async request<T>(options: RestRequestOptions): Promise<ApiSuccessEnvelope<T>> {
    const normalizedPath = options.path.startsWith("/") ? options.path : `/${options.path}`;
    const url = `${this.baseUrl}${normalizedPath}`;
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...options.headers,
    };

    const token = this.getAuthToken();
    if (token !== null && token.trim().length > 0) {
      headers["Authorization"] = `Bearer ${token.trim()}`;
    }

    if (options.method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["Idempotency-Key"] = options.idempotencyKey ?? this.generateKey();
    }

    const res = await this.fetchFn(url, {
      method: options.method,
      headers,
      ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
    });

    if (!res.ok) {
      let parsedErr: unknown = null;
      try {
        parsedErr = await res.json();
      } catch {
        parsedErr = null;
      }
      throw new ProblemDetailsError(
        normalizeProblemPayload(parsedErr, res.status, normalizedPath),
      );
    }

    const parsedBody = (await res.json()) as unknown;
    if (isRecord(parsedBody) && "data" in parsedBody && "meta" in parsedBody) {
      return parsedBody as unknown as ApiSuccessEnvelope<T>;
    }
    return {
      data: parsedBody as T,
      meta: {
        request_id: "direct",
        trace_id: "direct",
        tenant_id: null,
        api_version: "v1",
        timestamp: new Date().toISOString(),
      },
    };
  }

  public async initiateScreeningBatch(
    payload: InitiateBatchRequestWire,
    idempotencyKey?: string,
  ): Promise<InitiateBatchResponseWire> {
    const res = await this.request<InitiateBatchResponseWire>({
      method: "POST",
      path: "/v1/screening-batches",
      body: payload as unknown as Readonly<Record<string, unknown>>,
      idempotencyKey: idempotencyKey ?? payload.idempotency_key,
    });
    return res.data;
  }

  public async getScreeningBatchStatus(batchId: string): Promise<ScreeningBatchStatusWire> {
    const res = await this.request<ScreeningBatchStatusWire>({
      method: "GET",
      path: `/v1/screening-batches/${encodeURIComponent(batchId)}`,
    });
    return res.data;
  }

  public async fetchComparisonMatrix(
    requisitionId: string,
    candidateProfileIds: readonly string[],
  ): Promise<BackendComparisonMatrixWire> {
    const res = await this.request<BackendComparisonMatrixWire>({
      method: "POST",
      path: `/v1/requisitions/${encodeURIComponent(requisitionId)}/comparison-matrix`,
      body: { candidate_profile_ids: candidateProfileIds },
    });
    return res.data;
  }

  public async recordRecruiterOverride(
    reportId: string,
    payload: RecruiterOverrideRequestWire,
    idempotencyKey?: string,
  ): Promise<EvaluationReportResponseWire> {
    const res = await this.request<EvaluationReportResponseWire>({
      method: "POST",
      path: `/v1/evaluations/${encodeURIComponent(reportId)}/overrides`,
      body: payload as unknown as Readonly<Record<string, unknown>>,
      ...(idempotencyKey !== undefined ? { idempotencyKey } : {}),
    });
    return res.data;
  }
}
