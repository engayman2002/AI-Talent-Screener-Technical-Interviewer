"use client";

import { useSyncExternalStore } from "react";
import type {
  ComparisonCoordinatorState,
  SelectedEvidenceDrawer,
} from "../../application/evaluation/comparison_coordinator";
import type { RawCandidateComparisonInput } from "../../domain/evaluation/comparison_math";
import type { RecommendationBand } from "../../domain/shared/types";
import { useFrontendServices } from "../providers/AppServicesProvider";

export interface ComparisonMatrixHook {
  readonly state: ComparisonCoordinatorState;
  loadFromBackend(params: {
    readonly requisitionId: string;
    readonly candidateProfileIds: readonly string[];
    readonly blindMode?: boolean;
  }): Promise<void>;
  computeLocalMatrix(params: {
    readonly requisitionId: string;
    readonly candidates: readonly RawCandidateComparisonInput[];
    readonly blindMode?: boolean;
  }): void;
  toggleBlindMode(): void;
  openEvidenceDrawer(evidence: SelectedEvidenceDrawer | null): void;
  submitOverride(params: {
    readonly reportId: string;
    readonly newBand: RecommendationBand;
    readonly justification: string;
  }): Promise<void>;
}

export function useComparisonMatrix(): ComparisonMatrixHook {
  const { comparisonCoordinator } = useFrontendServices();
  const state = useSyncExternalStore(
    comparisonCoordinator.subscribe,
    comparisonCoordinator.getSnapshot,
    comparisonCoordinator.getSnapshot,
  );

  return {
    state,
    loadFromBackend: async (params) => {
      await comparisonCoordinator.loadFromBackend(params);
    },
    computeLocalMatrix: (params) => {
      comparisonCoordinator.computeLocalMatrix(params);
    },
    toggleBlindMode: () => comparisonCoordinator.toggleBlindMode(),
    openEvidenceDrawer: (ev) => comparisonCoordinator.openEvidenceDrawer(ev),
    submitOverride: async (params) => {
      await comparisonCoordinator.submitRecruiterOverride(params);
    },
  };
}
