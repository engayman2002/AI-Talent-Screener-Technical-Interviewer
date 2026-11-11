"""Persistence infrastructure adapter exports."""

from talent_screener.infrastructure.persistence.in_memory import (
    InMemoryCandidateProfileRepository,
    InMemoryConsentRecordRepository,
    InMemoryDistributedLock,
    InMemoryEvaluationReportRepository,
    InMemoryIdempotencyStore,
    InMemoryInterviewSessionRepository,
    InMemoryJobRequisitionRepository,
    InMemoryScreeningBatchRepository,
    InMemorySemanticMatchResultRepository,
    InMemoryTenantAccountRepository,
    InMemoryUnitOfWork,
)
from talent_screener.infrastructure.persistence.object_storage import (
    StoredBlobRecord,
    TenantScopedObjectStorageAdapter,
)
from talent_screener.infrastructure.persistence.vector_store import (
    ChildVectorPoint,
    InMemoryHybridVectorStoreBackend,
    ParentChunkPayload,
    VectorStoreBackendProtocol,
)

__all__ = [
    "ChildVectorPoint",
    "InMemoryCandidateProfileRepository",
    "InMemoryConsentRecordRepository",
    "InMemoryDistributedLock",
    "InMemoryEvaluationReportRepository",
    "InMemoryHybridVectorStoreBackend",
    "InMemoryIdempotencyStore",
    "InMemoryInterviewSessionRepository",
    "InMemoryJobRequisitionRepository",
    "InMemoryScreeningBatchRepository",
    "InMemorySemanticMatchResultRepository",
    "InMemoryTenantAccountRepository",
    "InMemoryUnitOfWork",
    "ParentChunkPayload",
    "StoredBlobRecord",
    "TenantScopedObjectStorageAdapter",
    "VectorStoreBackendProtocol",
]
