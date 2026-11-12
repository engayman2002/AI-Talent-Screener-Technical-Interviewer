"use client";

import { useSyncExternalStore } from "react";
import type { DemoBootstrapState } from "../../application/ops/demo_bootstrap_coordinator";
import type {
  DemoSeedManifestWire,
  SimulateDemoBatchResponseWire,
} from "../../application/ports/outbound";
import { useFrontendServices } from "../providers/AppServicesProvider";

export interface DemoBootstrapHook {
  readonly state: DemoBootstrapState;
  bootstrap(forceRefresh?: boolean): Promise<DemoSeedManifestWire | null>;
  refreshTelemetry(): Promise<void>;
  simulateLiveBatch(): Promise<SimulateDemoBatchResponseWire>;
  setActiveRole(role: "recruiter" | "candidate"): void;
}

export function useDemoBootstrap(): DemoBootstrapHook {
  const { demoCoordinator } = useFrontendServices();
  const state = useSyncExternalStore(
    demoCoordinator.subscribe,
    demoCoordinator.getSnapshot,
    demoCoordinator.getSnapshot,
  );

  return {
    state,
    bootstrap: (forceRefresh) => demoCoordinator.bootstrap(forceRefresh),
    refreshTelemetry: () => demoCoordinator.refreshTelemetry(),
    simulateLiveBatch: () => demoCoordinator.simulateLiveBatch(),
    setActiveRole: (role) => demoCoordinator.setActiveRole(role),
  };
}
