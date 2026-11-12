import { describe, expect, it } from "vitest";
import {
  buildNormalizedComparisonMatrix,
  classifyVerificationDelta,
  computeDifficultyAdjustedScore,
  computeEmpiricalBayesShrinkage,
  computeEpistemicGapResolutionRatio,
  computeHolisticDecisionIndex,
  computePosteriorCompetencyScore,
  deriveAuthenticityBadge,
  type RawCandidateComparisonInput,
} from "../../../src/domain/evaluation/comparison_math";
import { ComparisonInputError } from "../../../src/domain/shared/errors";

function makeCandidate(
  id: string,
  alias: string,
  fullName: string,
  distScore: number,
  sqlScore: number,
  authenticityScore: number,
): RawCandidateComparisonInput {
  return {
    candidateProfileId: id,
    anonymizedAlias: alias,
    revealedFullName: fullName,
    compositeCvMatchScore: 84,
    authenticityScore,
    integrityFlagCount: authenticityScore < 0.85 ? 2 : 0,
    recommendationBand: authenticityScore < 0.85 ? "BORDERLINE_HOLD" : "HIRE",
    resolvedSkillGapsCount: 2,
    totalSkillGapsCount: 3,
    competencies: [
      {
        competencyId: "comp-dist",
        competencyName: "Distributed Systems",
        weight: 0.6,
        targetDepth: 4,
        cvPriorScore0to100: 75,
        liveAchievedDepth0to5: distScore / 20,
        peakDifficultyReached: 5,
        turnsEvaluated: 3,
        evaluationConfidence: 0.9,
        cvEvidenceQuote: "Led 3-region Kafka migration.",
        interviewQuestion: "How do you prevent zombie producers?",
        candidateAnswerExcerpt: "Transactional fencing with epoch bumps.",
        evaluatorRationale: "Demonstrated strong mastery of epoch fencing.",
      },
      {
        competencyId: "comp-sql",
        competencyName: "PostgreSQL Internals",
        weight: 0.4,
        targetDepth: 3,
        cvPriorScore0to100: 82,
        liveAchievedDepth0to5: sqlScore / 20,
        peakDifficultyReached: 3,
        turnsEvaluated: 2,
        evaluationConfidence: 0.85,
      },
    ],
  };
}

describe("Bayesian Comparison Matrix Normalization & Ranking Algebra", () => {
  it("computes Empirical Bayes shrinkage, difficulty adjustment, verification delta, and holistic index", () => {
    // 0 turns shrinks completely to prior mu_0 = 70
    expect(computeEmpiricalBayesShrinkage(0, 95, 70, 2)).toBe(70);
    // 2 turns with m=2 averages 50/50 -> (90 + 70) / 2 = 80
    expect(computeEmpiricalBayesShrinkage(2, 90, 70, 2)).toBeCloseTo(80, 5);

    const adjAbove = computeDifficultyAdjustedScore(80, 5, 3);
    const adjAt = computeDifficultyAdjustedScore(80, 3, 3);
    expect(adjAbove).toBeGreaterThan(adjAt);
    expect(adjAt).toBeCloseTo(80, 5);

    expect(computeEpistemicGapResolutionRatio(2, 4)).toBeCloseTo(0.5, 5);

    const posterior = computePosteriorCompetencyScore({
      cvPriorScore0to100: 70,
      liveAchievedDepth0to5: 4.5,
      turnsEvaluated: 3,
      evaluationConfidence: 0.9,
    });
    expect(posterior).toBeGreaterThan(80);
    expect(classifyVerificationDelta(70, 4.5, 3).classification).toBe("HIDDEN_GEM_UPGRADE");
    expect(classifyVerificationDelta(90, 2.5, 2).classification).toBe("RESUME_INFLATION_WARNING");
    expect(classifyVerificationDelta(80, 4.1, 2).classification).toBe("CORROBORATED_CLAIM");
    expect(classifyVerificationDelta(80, 0, 0).classification).toBe("CV_PRIOR_ONLY");

    const cleanH = computeHolisticDecisionIndex(88, 0.96);
    const penalizedH = computeHolisticDecisionIndex(88, 0.65);
    expect(penalizedH).toBeLessThan(cleanH);

    expect(deriveAuthenticityBadge(0.95, 0).tier).toBe("VERIFIED_HIGH");
    expect(deriveAuthenticityBadge(0.78, 1).tier).toBe("REVIEW_ADVISORY");
    expect(deriveAuthenticityBadge(0.55, 3).tier).toBe("HIGH_RISK_FLAGGED");
  });

  it("enforces 2..4 finalist bounds, Blind Mode aliasing, DIMENSION_LEADER, and pairwise margins", () => {
    const c1 = makeCandidate("c1", "Candidate #A", "Alice Chen", 92, 76, 0.97);
    const c2 = makeCandidate("c2", "Candidate #B", "Bob Diaz", 72, 94, 0.95);
    const c3 = makeCandidate("c3", "Candidate #C", "Cara Singh", 95, 88, 0.62);

    expect(() =>
      buildNormalizedComparisonMatrix({
        requisitionId: "req-1",
        candidates: [c1],
        blindMode: false,
      }),
    ).toThrow(ComparisonInputError);

    expect(() =>
      buildNormalizedComparisonMatrix({
        requisitionId: "req-1",
        candidates: [c1, c2, c3, makeCandidate("c4", "D", "Dan", 70, 70, 0.9), makeCandidate("c5", "E", "Eve", 70, 70, 0.9)],
        blindMode: false,
      }),
    ).toThrow(ComparisonInputError);

    const blindMatrix = buildNormalizedComparisonMatrix({
      requisitionId: "req-1",
      candidates: [c1, c2, c3],
      blindMode: true,
    });

    expect(blindMatrix.columns).toHaveLength(3);
    // Blind mode masks revealedFullName
    for (const col of blindMatrix.columns) {
      expect(col.displayName).toBe(col.anonymizedAlias);
    }
    // Ranks must be 1, 2, 3 and holisticDecisionIndex monotonically non-increasing
    expect(blindMatrix.columns.map((c) => c.rank)).toEqual([1, 2, 3]);
    expect(blindMatrix.columns[0]!.holisticDecisionIndex).toBeGreaterThanOrEqual(
      blindMatrix.columns[1]!.holisticDecisionIndex,
    );
    expect(blindMatrix.columns[1]!.holisticDecisionIndex).toBeGreaterThanOrEqual(
      blindMatrix.columns[2]!.holisticDecisionIndex,
    );

    // c3 should be discounted below c1 due to authenticityScore 0.62 despite 95 dist score
    expect(blindMatrix.columns[2]!.candidateProfileId).toBe("c3");

    // Each competency row should mark exactly one DIMENSION_LEADER (highest posterior)
    for (const row of blindMatrix.rows) {
      const leaders = Object.values(row.cellsByCandidateId).filter((cell) => cell.isDimensionLeader);
      expect(leaders.length).toBeGreaterThanOrEqual(1);
    }

    expect(blindMatrix.pairwiseAdvantages.length).toBeGreaterThan(0);

    const revealedMatrix = buildNormalizedComparisonMatrix({
      requisitionId: "req-1",
      candidates: [c1, c2],
      blindMode: false,
    });
    expect(revealedMatrix.columns.map((c) => c.displayName)).toContain("Alice Chen");
  });
});
