"""Document Intelligence & Semantic Matching Bounded Context public exports."""

from talent_screener.domain.contexts.document_intelligence.aggregates import (
    CandidateProfile,
    ScreeningBatch,
)
from talent_screener.domain.contexts.document_intelligence.entities import (
    DocumentIngestionTask,
    ProjectComplexityRecord,
    WorkExperienceEntry,
)
from talent_screener.domain.contexts.document_intelligence.events import (
    CandidateProfileNormalizedEvent,
    DocumentExtractionFailedEvent,
    DocumentExtractionSucceededEvent,
    ScreeningBatchCompletedEvent,
    ScreeningBatchCreatedEvent,
)
from talent_screener.domain.contexts.document_intelligence.exceptions import (
    BatchCountMismatchError,
    BatchQuotaExceededError,
    DuplicateFileInBatchError,
    InvalidDateRangeError,
    InvalidMatchWeightsError,
    UngroundedClaimError,
)
from talent_screener.domain.contexts.document_intelligence.matching import (
    CandidateMatchedToJobEvent,
    CandidateRejectedByGatekeeperEvent,
    CandidateShortlistedForInterviewEvent,
    CandidateTier,
    ChunkEvidenceSample,
    CompetencyAlignmentItem,
    CompetencyEvaluationSpec,
    EpistemicSkillGap,
    GapType,
    HybridMatchingDomainService,
    HybridMatchVector,
    MatchThresholds,
    RecencyDecayFactor,
    SemanticMatchResult,
    SemanticMatchResultRepositoryPort,
)
from talent_screener.domain.contexts.document_intelligence.repositories import (
    CandidateProfileRepositoryPort,
    ScreeningBatchRepositoryPort,
)
from talent_screener.domain.contexts.document_intelligence.value_objects import (
    BatchStatus,
    ExtractedSkillClaim,
    FileContentHash,
    ImpactMetric,
    PiiRedactionMap,
    SeniorityEstimate,
    TaskIngestionStatus,
)

__all__ = [
    "BatchCountMismatchError",
    "BatchQuotaExceededError",
    "BatchStatus",
    "CandidateMatchedToJobEvent",
    "CandidateProfile",
    "CandidateProfileNormalizedEvent",
    "CandidateProfileRepositoryPort",
    "CandidateRejectedByGatekeeperEvent",
    "CandidateShortlistedForInterviewEvent",
    "CandidateTier",
    "ChunkEvidenceSample",
    "CompetencyAlignmentItem",
    "CompetencyEvaluationSpec",
    "DocumentExtractionFailedEvent",
    "DocumentExtractionSucceededEvent",
    "DocumentIngestionTask",
    "DuplicateFileInBatchError",
    "EpistemicSkillGap",
    "ExtractedSkillClaim",
    "FileContentHash",
    "GapType",
    "HybridMatchVector",
    "HybridMatchingDomainService",
    "ImpactMetric",
    "InvalidDateRangeError",
    "InvalidMatchWeightsError",
    "MatchThresholds",
    "PiiRedactionMap",
    "ProjectComplexityRecord",
    "RecencyDecayFactor",
    "ScreeningBatch",
    "ScreeningBatchCompletedEvent",
    "ScreeningBatchCreatedEvent",
    "ScreeningBatchRepositoryPort",
    "SemanticMatchResult",
    "SemanticMatchResultRepositoryPort",
    "SeniorityEstimate",
    "TaskIngestionStatus",
    "UngroundedClaimError",
    "WorkExperienceEntry",
]
