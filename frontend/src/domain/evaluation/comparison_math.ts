/**
 * Pure Candidate Comparison Matrix Bayesian Shrinkage Normalization & Ranking Algebra.
 * Implements Phase 7 Section 3.2 mathematical formulations without framework dependencies.
 */

import { ComparisonInputError } from "../shared/errors";
import type {
  AuthenticityBadgeView,
  DifficultyLevel,
  RecommendationBand,
  VerificationClassification,
} from "../shared/types";

export const PRIOR_INERTIA_TAU0 = 0.5;
export const AUTHENTICITY_SAFETY_THRESHOLD = 0.85;
export const AUTHENTICITY_PENALTY_EXPONENT = 1.5;

export interface RawCompetencyObservation {
  readonly competencyId: string;
  readonly competencyName: string;
  readonly weight: number;
  readonly targetDepth: DifficultyLevel;
  readonly cvPriorScore0to100: number;
  readonly liveAchievedDepth0to5: number;
  readonly peakDifficultyReached: DifficultyLevel;
  readonly turnsEvaluated: number;
  readonly evaluationConfidence: number;
  readonly cvEvidenceQuote?: string;
  readonly interviewQuestion?: string;
  readonly candidateAnswerExcerpt?: string;
  readonly evaluatorRationale?: string;
}

export interface RawCandidateComparisonInput {
  readonly candidateProfileId: string;
  readonly anonymizedAlias: string;
  readonly revealedFullName?: string;
  readonly compositeCvMatchScore: number;
  readonly authenticityScore: number;
  readonly integrityFlagCount: number;
  readonly recommendationBand: RecommendationBand;
  readonly resolvedSkillGapsCount: number;
  readonly totalSkillGapsCount: number;
  readonly competencies: readonly RawCompetencyObservation[];
}

export interface EmpiricalBayesParams {
  readonly populationPriorMean?: number;
  readonly shrinkageM?: number;
  readonly tau0?: number;
}

export interface NormalizedCompetencyCell {
  readonly competencyId: string;
  readonly candidateProfileId: string;
  readonly empiricalBayesScore: number;
  readonly posteriorScore: number;
  readonly difficultyAdjustedScore: number;
  readonly achievedDepth: number;
  readonly targetDepth: DifficultyLevel;
  readonly peakDifficultyReached: DifficultyLevel;
  readonly turnsEvaluated: number;
  readonly verificationDelta: number;
  readonly verificationClassification: VerificationClassification;
  readonly isDimensionLeader: boolean;
  readonly cvEvidenceQuote: string;
  readonly interviewQuestion: string;
  readonly candidateAnswerExcerpt: string;
  readonly evaluatorRationale: string;
}

export interface NormalizedCandidateColumn {
  readonly candidateProfileId: string;
  readonly displayName: string;
  readonly anonymizedAlias: string;
  readonly compositeCvMatchScore: number;
  readonly rawPosteriorTechnicalScore: number;
  readonly holisticDecisionIndex: number;
  readonly difficultyAdjustedComposite: number;
  readonly epistemicGapResolutionRatio: number;
  readonly authenticityBadge: AuthenticityBadgeView;
  readonly recommendationBand: RecommendationBand;
  readonly rank: number;
}

export interface CompetencyDimensionHeader {
  readonly competencyId: string;
  readonly competencyName: string;
  readonly normalizedWeightPercent: number;
  readonly targetDepth: DifficultyLevel;
}

export interface PairwiseAdvantageCell {
  readonly competencyId: string;
  readonly candidateAId: string;
  readonly candidateBId: string;
  readonly marginOmega: number;
}

export interface SideBySideComparisonMatrixViewModel {
  readonly requisitionId: string;
  readonly blindMode: boolean;
  readonly columns: readonly NormalizedCandidateColumn[];
  readonly rows: readonly {
    readonly dimension: CompetencyDimensionHeader;
    readonly cellsByCandidateId: Readonly<Record<string, NormalizedCompetencyCell>>;
  }[];
  readonly pairwiseAdvantages: readonly PairwiseAdvantageCell[];
}

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.max(min, Math.min(max, value));
}

/**
 * Empirical Bayes Shrinkage Estimator:
 * theta_hat_{c,k} = (n / (n + m)) * S_bar + (m / (n + m)) * mu_0.
 */
export function computeEmpiricalBayesShrinkage(
  turnsCount: number,
  rawMeanScore0to100: number,
  populationPriorMean = 70,
  shrinkageConstantM = 2.0,
): number {
  const n = Math.max(0, turnsCount);
  const m = Math.max(0, shrinkageConstantM);
  const s = clamp(rawMeanScore0to100, 0, 100);
  const mu0 = clamp(populationPriorMean, 0, 100);
  if (n + m <= 0) {
    return s;
  }
  return (n / (n + m)) * s + (m / (n + m)) * mu0;
}

/**
 * Difficulty-adjusted calibration scaling across L1..L5 peak difficulty relative to target depth.
 */
export function computeDifficultyAdjustedScore(
  posteriorScore0to100: number,
  peakDifficulty: DifficultyLevel,
  targetDepth: DifficultyLevel,
): number {
  const delta = peakDifficulty - targetDepth;
  const multiplier = clamp(1.0 + delta * 0.04, 0.88, 1.08);
  return clamp(posteriorScore0to100 * multiplier, 0, 100);
}

/**
 * Epistemic Skill-Gap Resolution Ratio in [0, 1].
 */
export function computeEpistemicGapResolutionRatio(
  resolvedCount: number,
  totalCount: number,
): number {
  if (totalCount <= 0) {
    return 1.0;
  }
  return clamp(resolvedCount / totalCount, 0, 1);
}

/**
 * Phase 7 Section 3.2.2 Posterior Competency Score S_k^post(c) in [0, 100]:
 * S_post = 100 * ((1 - rho) * C_cv + rho * I_live), rho = (n * gamma) / (tau_0 + n * gamma).
 */
export function computePosteriorCompetencyScore(params: {
  readonly cvPriorScore0to100: number;
  readonly liveAchievedDepth0to5: number;
  readonly turnsEvaluated: number;
  readonly evaluationConfidence: number;
  readonly tau0?: number;
}): number {
  const cvNorm = clamp(params.cvPriorScore0to100 / 100, 0, 1);
  const liveNorm = clamp(params.liveAchievedDepth0to5 / 5, 0, 1);
  const n = Math.max(0, params.turnsEvaluated);
  const gamma = clamp(params.evaluationConfidence, 0, 1);
  const tau0 = Math.max(1e-6, params.tau0 ?? PRIOR_INERTIA_TAU0);
  const effectiveWeight = n * gamma;
  const rho = effectiveWeight > 0 ? effectiveWeight / (tau0 + effectiveWeight) : 0;
  return 100 * ((1 - rho) * cvNorm + rho * liveNorm);
}

/**
 * Epistemic Verification Delta Delta_verify(c, k) = I_live - C_cv in [-1, +1]
 * and deterministic classification tag.
 */
export function classifyVerificationDelta(
  cvPriorScore0to100: number,
  liveAchievedDepth0to5: number,
  turnsEvaluated: number,
): { readonly delta: number; readonly classification: VerificationClassification } {
  const cvNorm = clamp(cvPriorScore0to100 / 100, 0, 1);
  if (turnsEvaluated <= 0) {
    return { delta: 0, classification: "CV_PRIOR_ONLY" };
  }
  const liveNorm = clamp(liveAchievedDepth0to5 / 5, 0, 1);
  const delta = clamp(liveNorm - cvNorm, -1, 1);
  if (delta >= 0.15) {
    return { delta, classification: "HIDDEN_GEM_UPGRADE" };
  }
  if (delta <= -0.25) {
    return { delta, classification: "RESUME_INFLATION_WARNING" };
  }
  return { delta, classification: "CORROBORATED_CLAIM" };
}

/**
 * Phase 7 Section 3.2.3 Integrity-Adjusted Holistic Decision Index H_final(c).
 */
export function computeHolisticDecisionIndex(
  weightedPosteriorTechnicalScore: number,
  authenticityScore: number,
): number {
  const base = clamp(weightedPosteriorTechnicalScore, 0, 100);
  const auth = clamp(authenticityScore, 0, 1);
  const penaltyFactor = Math.min(
    1.0,
    Math.pow(auth / AUTHENTICITY_SAFETY_THRESHOLD, AUTHENTICITY_PENALTY_EXPONENT),
  );
  return clamp(base * penaltyFactor, 0, 100);
}

export function deriveAuthenticityBadge(
  score: number,
  flagCount: number,
): AuthenticityBadgeView {
  const s = clamp(score, 0, 1);
  const tier =
    s >= 0.85
      ? "VERIFIED_HIGH"
      : s >= 0.6
        ? "REVIEW_ADVISORY"
        : "HIGH_RISK_FLAGGED";
  return {
    score: s,
    tier,
    flagCount: Math.max(0, Math.round(flagCount)),
  };
}

/**
 * Synthesize normalized 2..4 candidate SideBySideComparisonMatrixViewModel
 * enforcing uniqueness, Bayesian shrinkage, holistic index sorting, and DIMENSION_LEADER flags.
 */
export function buildNormalizedComparisonMatrix(params: {
  readonly requisitionId: string;
  readonly candidates: readonly RawCandidateComparisonInput[];
  readonly blindMode?: boolean;
  readonly bayes?: EmpiricalBayesParams;
}): SideBySideComparisonMatrixViewModel {
  const count = params.candidates.length;
  if (count < 2 || count > 4) {
    throw new ComparisonInputError(
      `Side-by-side comparison requires between 2 and 4 candidates; got ${count}.`,
    );
  }
  const seenIds = new Set<string>();
  for (const cand of params.candidates) {
    if (seenIds.has(cand.candidateProfileId)) {
      throw new ComparisonInputError(
        `Duplicate candidateProfileId ${cand.candidateProfileId} in comparison set.`,
        "DUPLICATE_COMPARISON_CANDIDATE",
      );
    }
    seenIds.add(cand.candidateProfileId);
  }

  const blindMode = params.blindMode ?? false;
  const priorMean = params.bayes?.populationPriorMean ?? 70;
  const shrinkageM = params.bayes?.shrinkageM ?? 2.0;
  const tau0 = params.bayes?.tau0 ?? PRIOR_INERTIA_TAU0;

  // Collect ordered competency dimensions from the first candidate (shared rubric schema)
  const dimensionOrder: CompetencyDimensionHeader[] = [];
  const seenDims = new Set<string>();
  for (const cand of params.candidates) {
    for (const obs of cand.competencies) {
      if (!seenDims.has(obs.competencyId)) {
        seenDims.add(obs.competencyId);
        dimensionOrder.push({
          competencyId: obs.competencyId,
          competencyName: obs.competencyName,
          normalizedWeightPercent: Number((obs.weight * 100).toFixed(1)),
          targetDepth: obs.targetDepth,
        });
      }
    }
  }
  dimensionOrder.sort((a, b) => b.normalizedWeightPercent - a.normalizedWeightPercent);

  const cellTempByDim = new Map<string, Map<string, NormalizedCompetencyCell>>();
  const candidateWeightsByDim = new Map<string, number>();

  for (const dim of dimensionOrder) {
    cellTempByDim.set(dim.competencyId, new Map());
  }

  const columnDrafts: Omit<NormalizedCandidateColumn, "rank">[] = [];

  for (const cand of params.candidates) {
    const obsByDim = new Map<string, RawCompetencyObservation>();
    for (const obs of cand.competencies) {
      obsByDim.set(obs.competencyId, obs);
      candidateWeightsByDim.set(obs.competencyId, obs.weight);
    }

    let weightedPosterior = 0;
    let weightedDiffAdj = 0;
    let totalWeight = 0;

    for (const dim of dimensionOrder) {
      const obs = obsByDim.get(dim.competencyId);
      if (obs === undefined) {
        continue;
      }
      const rawLiveScore = (obs.liveAchievedDepth0to5 / 5) * 100;
      const empiricalBayesScore = computeEmpiricalBayesShrinkage(
        obs.turnsEvaluated,
        obs.turnsEvaluated > 0 ? rawLiveScore : obs.cvPriorScore0to100,
        priorMean,
        shrinkageM,
      );
      const posteriorScore = computePosteriorCompetencyScore({
        cvPriorScore0to100: obs.cvPriorScore0to100,
        liveAchievedDepth0to5: obs.liveAchievedDepth0to5,
        turnsEvaluated: obs.turnsEvaluated,
        evaluationConfidence: obs.evaluationConfidence,
        tau0,
      });
      const difficultyAdjustedScore = computeDifficultyAdjustedScore(
        posteriorScore,
        obs.peakDifficultyReached,
        obs.targetDepth,
      );
      const { delta, classification } = classifyVerificationDelta(
        obs.cvPriorScore0to100,
        obs.liveAchievedDepth0to5,
        obs.turnsEvaluated,
      );

      weightedPosterior += obs.weight * posteriorScore;
      weightedDiffAdj += obs.weight * difficultyAdjustedScore;
      totalWeight += obs.weight;

      const dimMap = cellTempByDim.get(dim.competencyId);
      dimMap?.set(cand.candidateProfileId, {
        competencyId: dim.competencyId,
        candidateProfileId: cand.candidateProfileId,
        empiricalBayesScore,
        posteriorScore,
        difficultyAdjustedScore,
        achievedDepth: obs.liveAchievedDepth0to5,
        targetDepth: obs.targetDepth,
        peakDifficultyReached: obs.peakDifficultyReached,
        turnsEvaluated: obs.turnsEvaluated,
        verificationDelta: delta,
        verificationClassification: classification,
        isDimensionLeader: false,
        cvEvidenceQuote: obs.cvEvidenceQuote ?? "",
        interviewQuestion: obs.interviewQuestion ?? "",
        candidateAnswerExcerpt: obs.candidateAnswerExcerpt ?? "",
        evaluatorRationale: obs.evaluatorRationale ?? "",
      });
    }

    const normFactor = totalWeight > 1e-6 ? 1 / totalWeight : 1;
    const rawTech = weightedPosterior * normFactor;
    const adjTech = weightedDiffAdj * normFactor;
    const holistic = computeHolisticDecisionIndex(rawTech, cand.authenticityScore);

    columnDrafts.push({
      candidateProfileId: cand.candidateProfileId,
      displayName: blindMode
        ? cand.anonymizedAlias
        : (cand.revealedFullName ?? cand.anonymizedAlias),
      anonymizedAlias: cand.anonymizedAlias,
      compositeCvMatchScore: clamp(cand.compositeCvMatchScore, 0, 100),
      rawPosteriorTechnicalScore: Number(rawTech.toFixed(2)),
      holisticDecisionIndex: Number(holistic.toFixed(2)),
      difficultyAdjustedComposite: Number(adjTech.toFixed(2)),
      epistemicGapResolutionRatio: computeEpistemicGapResolutionRatio(
        cand.resolvedSkillGapsCount,
        cand.totalSkillGapsCount,
      ),
      authenticityBadge: deriveAuthenticityBadge(
        cand.authenticityScore,
        cand.integrityFlagCount,
      ),
      recommendationBand: cand.recommendationBand,
    });
  }

  columnDrafts.sort((a, b) => b.holisticDecisionIndex - a.holisticDecisionIndex);
  const columns: NormalizedCandidateColumn[] = columnDrafts.map((col, idx) => ({
    ...col,
    rank: idx + 1,
  }));

  // Mark DIMENSION_LEADER where candidate has highest posteriorScore AND achievedDepth >= targetDepth
  const rows = dimensionOrder.map((dim) => {
    const dimMap = cellTempByDim.get(dim.competencyId) ?? new Map();
    let maxScore = -Infinity;
    for (const cell of dimMap.values()) {
      if (cell.posteriorScore > maxScore) {
        maxScore = cell.posteriorScore;
      }
    }
    const cellsRecord: Record<string, NormalizedCompetencyCell> = {};
    for (const [cid, cell] of dimMap.entries()) {
      const isLeader =
        cell.posteriorScore === maxScore &&
        maxScore > 0 &&
        cell.achievedDepth >= cell.targetDepth;
      cellsRecord[cid] = { ...cell, isDimensionLeader: isLeader };
    }
    return {
      dimension: dim,
      cellsByCandidateId: cellsRecord,
    };
  });

  // Cross-candidate relative advantage Omega_{a,b}^(k) = w_k * (S_post(a) - S_post(b))
  const pairwiseAdvantages: PairwiseAdvantageCell[] = [];
  for (const row of rows) {
    const dimId = row.dimension.competencyId;
    const weight = candidateWeightsByDim.get(dimId) ?? row.dimension.normalizedWeightPercent / 100;
    for (let i = 0; i < columns.length; i += 1) {
      for (let j = 0; j < columns.length; j += 1) {
        if (i === j) {
          continue;
        }
        const colA = columns[i];
        const colB = columns[j];
        if (colA === undefined || colB === undefined) {
          continue;
        }
        const cellA = row.cellsByCandidateId[colA.candidateProfileId];
        const cellB = row.cellsByCandidateId[colB.candidateProfileId];
        if (cellA !== undefined && cellB !== undefined) {
          pairwiseAdvantages.push({
            competencyId: dimId,
            candidateAId: colA.candidateProfileId,
            candidateBId: colB.candidateProfileId,
            marginOmega: Number((weight * (cellA.posteriorScore - cellB.posteriorScore)).toFixed(2)),
          });
        }
      }
    }
  }

  return {
    requisitionId: params.requisitionId,
    blindMode,
    columns,
    rows,
    pairwiseAdvantages,
  };
}
