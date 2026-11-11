"""Conceptual DTO contracts for Document Intelligence & Hybrid Vector Search ports."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.value_objects import (
    ExtractedSkillClaim,
    ImpactMetric,
    PiiRedactionMap,
    SeniorityEstimate,
)
from talent_screener.domain.shared.value_objects import (
    ConfidenceScore,
    SHA256Hash,
    TenantId,
)


@dataclass(frozen=True, slots=True)
class DocumentExtractionCommandDTO:
    """Command payload passed across DocumentParserPort (Phase 1 Section 5.1)."""

    tenant_id: TenantId
    batch_id: UUID
    batch_file_id: UUID
    document_stream_uri: str
    mime_type: str
    expected_sha256: SHA256Hash
    skill_taxonomy_version: str = "1.0.0"


@dataclass(frozen=True, slots=True)
class ExtractedWorkExperienceDTO:
    """Structured chronological employment DTO extracted by infrastructure ACL."""

    role_title: str
    normalized_role: str
    company_token: str
    start_year: int
    start_month: int
    end_year: int | None
    end_month: int | None
    is_current: bool = False
    employment_type: str = "FULL_TIME"


@dataclass(frozen=True, slots=True)
class ExtractedProjectDTO:
    """Structured technical project artifact extracted with verbatim quote."""

    project_name: str
    scale_tier: str
    summary: str
    source_quote: str
    impact_metrics: tuple[ImpactMetric, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractedCandidateDossierDTO:
    """Zero-PII extracted candidate dossier returned across DocumentParserPort."""

    anonymized_candidate_token: UUID
    schema_version: str
    raw_redacted_text: str
    pii_redaction_map: PiiRedactionMap
    work_experiences: tuple[ExtractedWorkExperienceDTO, ...]
    projects: tuple[ExtractedProjectDTO, ...]
    skill_claims: tuple[ExtractedSkillClaim, ...]
    seniority_estimate: SeniorityEstimate
    extraction_confidence: ConfidenceScore


@dataclass(frozen=True, slots=True)
class CompetencyQueryDescriptorDTO:
    """Query descriptor representing one rubric dimension for hybrid vector retrieval."""

    competency_id: UUID
    canonical_skill_id: str
    ideal_anchor_descriptor: str
    weight: float
    is_gatekeeper: bool = False
    min_applied_months: int = 0
    strict_gatekeeper: bool = True


@dataclass(frozen=True, slots=True)
class RetrievedChunkScoreDTO:
    """Normalized dense + sparse retrieval outcome for a single candidate child chunk."""

    chunk_id: str
    parent_entry_id: str
    dense_similarity: float
    sparse_bm25_score: float
    recency_months_delta: int
    evidentiary_depth: int
    verbatim_excerpt: str


@dataclass(frozen=True, slots=True)
class CompetencyRetrievalEvidenceDTO:
    """Top-K retrieved evidence chunks for one competency criterion."""

    competency_id: UUID
    retrieved_chunks: tuple[RetrievedChunkScoreDTO, ...]


@dataclass(frozen=True, slots=True)
class CandidateRetrievalEvidenceBundleDTO:
    """Complete tenant-scoped retrieval bundle returned by HybridVectorSearchPort."""

    tenant_id: TenantId
    requisition_id: UUID
    candidate_profile_id: UUID
    competency_evidence: tuple[CompetencyRetrievalEvidenceDTO, ...]
