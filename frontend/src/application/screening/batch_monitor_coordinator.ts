/**
 * Screening Batch Upload & 60 FPS Coalesced SSE Monitor Coordinator.
 * Enforces 500-file quota and invariant totalFiles === completed + failed + pending.
 */

import { ValidationDomainError } from "../../domain/shared/errors";
import type { BatchStatus, TaskIngestionStatus } from "../../domain/shared/types";
import type {
  BatchFileManifestWire,
  BatchSseStreamPort,
  CoalescedBatchProgressSnapshot,
  InitiateBatchResponseWire,
  ProblemDetailsPayload,
  RestApiClientPort,
} from "../ports/outbound";
import { ProblemDetailsError } from "../ports/outbound";
import { ObservableStore, type UnsubscribeFn } from "../shared/store";

export const MAX_FILES_PER_SCREENING_BATCH = 500;
export const MAX_CV_FILE_SIZE_BYTES = 15 * 1024 * 1024;
const ALLOWED_EXTENSIONS = new Set([".pdf", ".docx", ".txt", ".md"]);

export interface BatchTaskViewItem {
  readonly taskId: string;
  readonly filename: string;
  readonly status: TaskIngestionStatus;
  readonly candidateProfileId: string | null;
  readonly errorCode: string | null;
}

export interface BatchMonitorState {
  readonly batchId: string | null;
  readonly requisitionId: string | null;
  readonly batchLabel: string;
  readonly batchStatus: BatchStatus;
  readonly totalFiles: number;
  readonly completedCount: number;
  readonly failedCount: number;
  readonly pendingCount: number;
  readonly latestEventId: number;
  readonly isStreaming: boolean;
  readonly tasks: readonly BatchTaskViewItem[];
  readonly problemError: ProblemDetailsPayload | null;
}

function isCompletedStatus(status: TaskIngestionStatus): boolean {
  return status === "COMPLETED" || status === "DUPLICATE_SKIPPED";
}

function isFailedStatus(status: TaskIngestionStatus): boolean {
  return status === "FAILED" || status === "QUARANTINED";
}

function deriveBatchMacroStatus(
  totalFiles: number,
  completedCount: number,
  failedCount: number,
  pendingCount: number,
  explicitStatus?: BatchStatus,
): BatchStatus {
  if (explicitStatus !== undefined) {
    return explicitStatus;
  }
  if (totalFiles === 0) {
    return "INITIATED";
  }
  if (pendingCount === 0) {
    if (failedCount === 0) {
      return "COMPLETED";
    }
    return completedCount > 0 ? "PARTIALLY_FAILED" : "FAILED";
  }
  return completedCount + failedCount > 0 ? "PROCESSING" : "QUEUED";
}

export function validateBatchManifest(files: readonly BatchFileManifestWire[]): void {
  if (files.length === 0 || files.length > MAX_FILES_PER_SCREENING_BATCH) {
    throw new ValidationDomainError(
      `Screening batch must include between 1 and ${MAX_FILES_PER_SCREENING_BATCH} files; got ${files.length}.`,
      "BATCH_QUOTA_EXCEEDED",
    );
  }
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i];
    if (file === undefined) {
      continue;
    }
    const lower = file.filename.trim().toLowerCase();
    const dotIdx = lower.lastIndexOf(".");
    const ext = dotIdx >= 0 ? lower.slice(dotIdx) : "";
    if (!ALLOWED_EXTENSIONS.has(ext)) {
      throw new ValidationDomainError(
        `Unsupported CV file extension for '${file.filename}'. Allowed: .pdf, .docx, .txt, .md.`,
        "UNSUPPORTED_FILE_TYPE",
      );
    }
    if (file.file_size_bytes <= 0 || file.file_size_bytes > MAX_CV_FILE_SIZE_BYTES) {
      throw new ValidationDomainError(
        `File '${file.filename}' size ${file.file_size_bytes} exceeds bounds (1..15 MB).`,
        "INVALID_FILE_SIZE",
      );
    }
    if (!/^[0-9a-f]{64}$/i.test(file.sha256_hex)) {
      throw new ValidationDomainError(
        `File '${file.filename}' missing valid 64-hex SHA-256 digest.`,
        "INVALID_SHA256_HASH",
      );
    }
  }
}

export class BatchMonitorCoordinator {
  private readonly store: ObservableStore<BatchMonitorState>;
  private readonly api: RestApiClientPort;
  private readonly sse: BatchSseStreamPort;
  private unsubscribeStream: UnsubscribeFn | null = null;

  public constructor(params: {
    readonly api: RestApiClientPort;
    readonly sse: BatchSseStreamPort;
  }) {
    this.api = params.api;
    this.sse = params.sse;
    this.store = new ObservableStore<BatchMonitorState>({
      batchId: null,
      requisitionId: null,
      batchLabel: "",
      batchStatus: "INITIATED",
      totalFiles: 0,
      completedCount: 0,
      failedCount: 0,
      pendingCount: 0,
      latestEventId: 0,
      isStreaming: false,
      tasks: [],
      problemError: null,
    });
  }

  public readonly getSnapshot = (): BatchMonitorState => this.store.getSnapshot();

  public readonly subscribe = (listener: () => void): UnsubscribeFn =>
    this.store.subscribe(listener);

  public async initiateAndMonitorBatch(params: {
    readonly requisitionId: string;
    readonly batchLabel: string;
    readonly files: readonly BatchFileManifestWire[];
    readonly idempotencyKey: string;
    readonly sseBasePath?: string;
  }): Promise<InitiateBatchResponseWire> {
    validateBatchManifest(params.files);
    try {
      const initiated = await this.api.initiateScreeningBatch(
        {
          requisition_id: params.requisitionId,
          batch_label: params.batchLabel,
          files: params.files,
          idempotency_key: params.idempotencyKey,
        },
        params.idempotencyKey,
      );

      const initialTasks: BatchTaskViewItem[] = initiated.upload_instructions.map((slot) => ({
        taskId: slot.task_id,
        filename: slot.filename,
        status: "PENDING",
        candidateProfileId: null,
        errorCode: null,
      }));

      this.store.setState({
        batchId: initiated.batch_id,
        requisitionId: initiated.requisition_id,
        batchLabel: params.batchLabel,
        batchStatus: initiated.status,
        totalFiles: initiated.total_files,
        completedCount: 0,
        failedCount: 0,
        pendingCount: initiated.total_files,
        latestEventId: 0,
        isStreaming: false,
        tasks: initialTasks,
        problemError: null,
      });

      const streamUrl =
        initiated.sse_stream_uri ??
        `${params.sseBasePath ?? ""}/v1/screening-batches/${initiated.batch_id}/events`;
      this.subscribeToBatchStream(initiated.batch_id, streamUrl);
      return initiated;
    } catch (err) {
      if (err instanceof ProblemDetailsError) {
        this.store.update((prev) => ({ ...prev, problemError: err.problem }));
      }
      throw err;
    }
  }

  public async refreshBatchSnapshot(batchId: string): Promise<void> {
    try {
      const status = await this.api.getScreeningBatchStatus(batchId);
      const tasks: BatchTaskViewItem[] = status.tasks.map((t) => ({
        taskId: t.task_id,
        filename: t.filename,
        status: t.status,
        candidateProfileId: t.candidate_profile_id,
        errorCode: t.error_code,
      }));
      this.store.update((prev) => ({
        ...prev,
        batchId: status.batch_id,
        requisitionId: status.requisition_id,
        batchLabel: status.batch_label,
        batchStatus: status.status,
        totalFiles: status.total_files,
        completedCount: status.completed_count,
        failedCount: status.failed_count,
        pendingCount: status.pending_count,
        tasks,
        problemError: null,
      }));
    } catch (err) {
      if (err instanceof ProblemDetailsError) {
        this.store.update((prev) => ({ ...prev, problemError: err.problem }));
      }
      throw err;
    }
  }

  public subscribeToBatchStream(batchId: string, streamUrl: string): void {
    this.stopMonitoring();
    this.store.update((prev) => ({ ...prev, batchId, isStreaming: true }));
    this.unsubscribeStream = this.sse.subscribe(
      {
        batchId,
        streamUrl,
        lastEventId: this.store.getSnapshot().latestEventId,
      },
      (coalesced: CoalescedBatchProgressSnapshot) => {
        this.applyCoalescedProgress(coalesced);
      },
      (err: Error) => {
        this.store.update((prev) => ({
          ...prev,
          isStreaming: false,
          problemError:
            err instanceof ProblemDetailsError ? err.problem : prev.problemError,
        }));
      },
    );
  }

  public applyCoalescedProgress(coalesced: CoalescedBatchProgressSnapshot): void {
    this.store.update((prev) => {
      const taskMap = new Map<string, BatchTaskViewItem>();
      for (const existing of prev.tasks) {
        taskMap.set(existing.taskId, existing);
      }

      for (const delta of coalesced.taskUpdates) {
        const prior = taskMap.get(delta.taskId);
        taskMap.set(delta.taskId, {
          taskId: delta.taskId,
          filename: delta.filename ?? prior?.filename ?? `${delta.taskId}.pdf`,
          status: delta.status,
          candidateProfileId:
            delta.candidateProfileId !== undefined
              ? delta.candidateProfileId
              : (prior?.candidateProfileId ?? null),
          errorCode:
            delta.errorCode !== undefined ? delta.errorCode : (prior?.errorCode ?? null),
        });
      }

      const tasks = Array.from(taskMap.values());
      const inferredTotal = Math.max(prev.totalFiles, tasks.length);
      let completedCount = 0;
      let failedCount = 0;
      for (const task of tasks) {
        if (isCompletedStatus(task.status)) {
          completedCount += 1;
        } else if (isFailedStatus(task.status)) {
          failedCount += 1;
        }
      }
      const pendingCount = Math.max(0, inferredTotal - completedCount - failedCount);
      const batchStatus = deriveBatchMacroStatus(
        inferredTotal,
        completedCount,
        failedCount,
        pendingCount,
        coalesced.batchStatus,
      );

      return {
        ...prev,
        batchId: coalesced.batchId,
        latestEventId: Math.max(prev.latestEventId, coalesced.latestEventId),
        totalFiles: inferredTotal,
        completedCount,
        failedCount,
        pendingCount,
        batchStatus,
        tasks,
      };
    });
  }

  public stopMonitoring(): void {
    if (this.unsubscribeStream !== null) {
      this.unsubscribeStream();
      this.unsubscribeStream = null;
    }
    this.store.update((prev) => ({ ...prev, isStreaming: false }));
  }
}
