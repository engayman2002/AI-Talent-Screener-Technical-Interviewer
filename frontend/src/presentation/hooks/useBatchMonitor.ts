"use client";

import { useSyncExternalStore } from "react";
import type { BatchFileManifestWire } from "../../application/ports/outbound";
import type { BatchMonitorState } from "../../application/screening/batch_monitor_coordinator";
import { useFrontendServices } from "../providers/AppServicesProvider";

export interface BatchMonitorHook {
  readonly state: BatchMonitorState;
  initiateBatch(params: {
    readonly requisitionId: string;
    readonly batchLabel: string;
    readonly files: readonly BatchFileManifestWire[];
    readonly idempotencyKey: string;
  }): Promise<void>;
  subscribeBatch(batchId: string, streamUrl: string): void;
  refreshSnapshot(batchId: string): Promise<void>;
  stop(): void;
}

export function useBatchMonitor(): BatchMonitorHook {
  const { batchCoordinator } = useFrontendServices();
  const state = useSyncExternalStore(
    batchCoordinator.subscribe,
    batchCoordinator.getSnapshot,
    batchCoordinator.getSnapshot,
  );

  return {
    state,
    initiateBatch: async (params) => {
      await batchCoordinator.initiateAndMonitorBatch(params);
    },
    subscribeBatch: (batchId, streamUrl) =>
      batchCoordinator.subscribeToBatchStream(batchId, streamUrl),
    refreshSnapshot: (batchId) => batchCoordinator.refreshBatchSnapshot(batchId),
    stop: () => batchCoordinator.stopMonitoring(),
  };
}
