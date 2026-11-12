/**
 * Shared Pure Domain Value Objects & Protocol-Aligned Enums.
 * Matches backend presentation contracts strictly without transport/framework coupling.
 */

export type DifficultyLevel = 1 | 2 | 3 | 4 | 5;

export type RecommendationBand =
  | "STRONG_HIRE"
  | "HIRE"
  | "BORDERLINE_HOLD"
  | "NO_HIRE";

export type BatchStatus =
  | "INITIATED"
  | "QUEUED"
  | "PROCESSING"
  | "COMPLETED"
  | "PARTIALLY_FAILED"
  | "FAILED"
  | "EXPIRED";

export type TaskIngestionStatus =
  | "PENDING"
  | "SCANNING"
  | "PARSING"
  | "REDACTING"
  | "EXTRACTING"
  | "INDEXING"
  | "COMPLETED"
  | "FAILED"
  | "QUARANTINED"
  | "DUPLICATE_SKIPPED";

export type RoutingAction =
  | "ESCALATE_DIFFICULTY"
  | "SOCRATIC_DEPTH_PROBE"
  | "SOCRATIC_AUTHENTICITY_PROBE"
  | "TRANSITION_NEXT_COMPETENCY"
  | "CONCLUDE_INTERVIEW";

export type VerificationClassification =
  | "HIDDEN_GEM_UPGRADE"
  | "CORROBORATED_CLAIM"
  | "RESUME_INFLATION_WARNING"
  | "CV_PRIOR_ONLY";

export type SessionStatus =
  | "SCHEDULED"
  | "INITIALIZING"
  | "IN_PROGRESS"
  | "EVALUATING_FINAL"
  | "COMPLETED"
  | "TERMINATED_EARLY"
  | "ABANDONED";

export type ConsentScope =
  | "AI_SCREENING"
  | "VOICE_INTERVIEW_RECORDING"
  | "INTEGRITY_TELEMETRY";

export interface AuthenticityBadgeView {
  readonly score: number;
  readonly tier: "VERIFIED_HIGH" | "REVIEW_ADVISORY" | "HIGH_RISK_FLAGGED";
  readonly flagCount: number;
}
