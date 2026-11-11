"""Semantic Matching Engine public exports."""

from talent_screener.domain.contexts.document_intelligence.matching.aggregates import (
    SemanticMatchResult,
)
from talent_screener.domain.contexts.document_intelligence.matching.domain_services import (
    HybridMatchingDomainService,
)
from talent_screener.domain.contexts.document_intelligence.matching.entities import (
    CompetencyAlignmentItem,
)
from talent_screener.domain.contexts.document_intelligence.matching.events import (
    CandidateMatchedToJobEvent,
    CandidateRejectedByGatekeeperEvent,
    CandidateShortlistedForInterviewEvent,
)
from talent_screener.domain.contexts.document_intelligence.matching.repositories import (
    SemanticMatchResultRepositoryPort,
)
from talent_screener.domain.contexts.document_intelligence.matching.value_objects import (
    CandidateTier,
    ChunkEvidenceSample,
    CompetencyEvaluationSpec,
    EpistemicSkillGap,
    GapType,
    HybridMatchVector,
    MatchThresholds,
    RecencyDecayFactor,
)

__all__ = [
    "CandidateMatchedToJobEvent",
    "CandidateRejectedByGatekeeperEvent",
    "CandidateShortlistedForInterviewEvent",
    "CandidateTier",
    "ChunkEvidenceSample",
    "CompetencyAlignmentItem",
    "CompetencyEvaluationSpec",
    "EpistemicSkillGap",
    "GapType",
    "HybridMatchVector",
    "HybridMatchingDomainService",
    "MatchThresholds",
    "RecencyDecayFactor",
    "SemanticMatchResult",
    "SemanticMatchResultRepositoryPort",
]
