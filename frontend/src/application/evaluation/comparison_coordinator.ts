/**
 * Side-by-Side Candidate Comparison Matrix Coordinator (Frontend Application Layer).
 * Validates 2..4 finalists, invokes REST comparison API, applies Bayesian normalization,
 * manages Blind Hiring Mode and Evidence Drilldown Drawer state, and handles overrides.
 */

import {
  buildNormalizedComparisonMatrix,
  type RawCandidateComparisonInput,
  type SideBySideComparisonMatrixViewModel,
} from "../../domain/evaluation/comparison_math";
import type { RecommendationBand } from "../../domain/shared/types";
import type {
  BackendComparisonMatrixWire,
  EvaluationReportResponseWire,
  ProblemDetailsPayload,
  RestApiClientPort,
} from "../ports/outbound";
import { ProblemDetailsError } from "../ports/outbound";
import { ObservableStore, type UnsubscribeFn } from "../shared/store";

export interface SelectedEvidenceDrawer {
  readonly competencyId: string;
  readonly competencyName: string;
  readonly candidateProfileId: string;
  readonly candidateLabel: string;
}

export interface ComparisonCoordinatorState {
  readonly isLoading: boolean;
  readonly blindMode: boolean;
  readonly matrix: SideBySideComparisonMatrixViewModel | null;
  readonly selectedEvidence: SelectedEvidenceDrawer | null;
  readonly lastOverrideReceipt: EvaluationReportResponseWire | null;
  readonly problemError: ProblemDetailsPayload | null;
}

export class ComparisonMatrixCoordinator {
  private readonly api: RestApiClientPort;
  private readonly store: ObservableStore<ComparisonCoordinatorState>;
  private lastRawInputs: readonly RawCandidateComparisonInput[] | null = null;

  public constructor(api: RestApiClientPort) {
    this.api = api;
    this.store = new ObservableStore<ComparisonCoordinatorState>({
      isLoading: false,
      blindMode: false,
      matrix: null,
      selectedEvidence: null,
      lastOverrideReceipt: null,
      problemError: null,
    });
  }

  public readonly getSnapshot = (): ComparisonCoordinatorState => this.store.getSnapshot();

  public readonly subscribe = (listener: () => void): UnsubscribeFn =>
    this.store.subscribe(listener);

  /**
   * Project raw domain comparison inputs directly or fetch from backend REST comparison API,
   * validating 2..4 candidate cardinality and computing Bayesian shrinkage normalization.
   */
  public computeLocalMatrix(params: {
    readonly requisitionId: string;
    readonly candidates: readonly RawCandidateComparisonInput[];
    readonly blindMode?: boolean;
  }): SideBySideComparisonMatrixViewModel {
    this.lastRawInputs = params.candidates;
    const effectiveBlind = params.blindMode ?? this.store.getSnapshot().blindMode;
    const matrix = buildNormalizedComparisonMatrix({
      requisitionId: params.requisitionId,
      candidates: params.candidates,
      blindMode: effectiveBlind,
    });
    this.store.update((prev) => ({
      ...prev,
      blindMode: effectiveBlind,
      matrix,
      problemError: null,
    }));
    return matrix;
  }

  public async loadFromBackend(params: {
    readonly requisitionId: string;
    readonly candidateProfileIds: readonly string[];
    readonly blindMode?: boolean;
    readonly displayNamesByCandidate?: Readonly<Record<string, string>>;
  }): Promise<SideBySideComparisonMatrixViewModel> {
    // Enforce domain invariant upfront before any network call
    const rawCheckCount = params.candidateProfileIds.length;
    if (rawCheckCount < 2 || rawCheckCount > 4) {
      buildNormalizedComparisonMatrix({
        requisitionId: params.requisitionId,
        candidates: [],
      });
    }

    this.store.update((prev) => ({ ...prev, isLoading: true, problemError: null }));
    try {
      const wire: BackendComparisonMatrixWire = await this.api.fetchComparisonMatrix(
        params.requisitionId,
        params.candidateProfileIds,
      );
      const rawInputs = this.translateWireToRawInputs(
        wire,
        params.displayNamesByCandidate ?? {},
      );
      const matrix = this.computeLocalMatrix({
        requisitionId: wire.requisition_id,
        candidates: rawInputs,
        ...(params.blindMode !== undefined ? { blindMode: params.blindMode } : {}),
      });
      this.store.update((prev) => ({ ...prev, isLoading: false, matrix }));
      return matrix;
    } catch (err) {
      this.store.update((prev) => ({
        ...prev,
        isLoading: false,
        problemError: err instanceof ProblemDetailsError ? err.problem : prev.problemError,
      }));
      throw err;
    }
  }

  public toggleBlindMode(): void {
    const prev = this.store.getSnapshot();
    const nextBlind = !prev.blindMode;
    if (this.lastRawInputs !== null && prev.matrix !== null) {
      const recomputed = buildNormalizedComparisonMatrix({
        requisitionId: prev.matrix.requisitionId,
        candidates: this.lastRawInputs,
        blindMode: nextBlind,
      });
      this.store.update((cur) => ({
        ...cur,
        blindMode: nextBlind,
        matrix: recomputed,
      }));
      return;
    }
    this.store.update((cur) => ({ ...cur, blindMode: nextBlind }));
  }

  public openEvidenceDrawer(evidence: SelectedEvidenceDrawer | null): void {
    this.store.update((prev) => ({ ...prev, selectedEvidence: evidence }));
  }

  public async submitRecruiterOverride(params: {
    readonly reportId: string;
    readonly newBand: RecommendationBand;
    readonly justification: string;
    readonly actorId?: string;
    readonly idempotencyKey?: string;
  }): Promise<EvaluationReportResponseWire> {
    this.store.update((prev) => ({ ...prev, isLoading: true, problemError: null }));
    try {
      const receipt = await this.api.recordRecruiterOverride(
        params.reportId,
        {
          new_band: params.newBand,
          justification: params.justification,
          ...(params.actorId !== undefined ? { actor_id: params.actorId } : {}),
        },
        params.idempotencyKey,
      );
      this.store.update((prev) => ({
        ...prev,
        isLoading: false,
        lastOverrideReceipt: receipt,
      }));
      return receipt;
    } catch (err) {
      this.store.update((prev) => ({
        ...prev,
        isLoading: false,
        problemError: err instanceof ProblemDetailsError ? err.problem : prev.problemError,
      }));
      throw err;
    }
  }

  private translateWireToRawInputs(
    wire: BackendComparisonMatrixWire,
    displayNamesByCandidate: Readonly<Record<string, string>>,
  ): readonly RawCandidateComparisonInput[] {
    const cellsByCand = new Map<string, BackendComparisonMatrixWire["dimension_cells"][number][]>();
    for (const cell of wire.dimension_cells) {
      const list = cellsByCand.get(cell.candidate_profile_id) ?? [];
      list.push(cell);
      cellsByCand.set(cell.candidate_profile_id, list);
    }

    return wire.candidates.map((cand, idx) => {
      const cells = cellsByCand.get(cand.candidate_profile_id) ?? [];
      const dimCount = Math.max(1, cells.length);
      const uniformWeight = 1 / dimCount;
      const fullName = displayNamesByCandidate[cand.candidate_profile_id];
      return {
        candidateProfileId: cand.candidate_profile_id,
        anonymizedAlias: `Candidate #${String.fromCharCode(65 + idx)}`,
        ...(fullName !== undefined ? { revealedFullName: fullName } : {}),
        compositeCvMatchScore: cand.cv_match_score,
        authenticityScore: cand.authenticity_score,
        integrityFlagCount: cand.authenticity_score < 0.85 ? 1 : 0,
        recommendationBand: cand.recommendation_band,
        resolvedSkillGapsCount: cells.filter(
          (c) => c.verification_classification !== "RESUME_INFLATION_WARNING",
        ).length,
        totalSkillGapsCount: cells.length,
        competencies: cells.map((c) => ({
          competencyId: c.competency_id,
          competencyName: c.competency_name,
          weight: uniformWeight,
          targetDepth: c.target_depth,
          cvPriorScore0to100: c.cv_prior_score,
          liveAchievedDepth0to5: c.achieved_depth,
          peakDifficultyReached: c.target_depth,
          turnsEvaluated: c.verification_classification === "CV_PRIOR_ONLY" ? 0 : 2,
          evaluationConfidence: 0.9,
        })),
      };
    });
  }
}
