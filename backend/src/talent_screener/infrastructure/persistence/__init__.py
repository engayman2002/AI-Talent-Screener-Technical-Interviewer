"""Persistence infrastructure adapter exports."""

from talent_screener.infrastructure.persistence.in_memory import (
    InMemoryCandidateProfileRepository,
    InMemoryConsentRecordRepository,
    InMemoryEvaluationReportRepository,
    InMemoryInterviewSessionRepository,
    InMemoryJobRequisitionRepository,
    InMemoryScreeningBatchRepository,
    InMemorySemanticMatchResultRepository,
    InMemoryTenantAccountRepository,
    InMemoryUnitOfWork,
)

__all__ = [
    "InMemoryCandidateProfileRepository",
    "InMemoryConsentRecordRepository",
    "InMemoryEvaluationReportRepository",
    "InMemoryInterviewSessionRepository",
    "InMemoryJobRequisitionRepository",
    "InMemoryScreeningBatchRepository",
    "InMemorySemanticMatchResultRepository",
    "InMemoryTenantAccountRepository",
    "InMemoryUnitOfWork",
]
