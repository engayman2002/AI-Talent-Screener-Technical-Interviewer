"""Unit tests for Document Intelligence and Semantic Matching Engine."""

from __future__ import annotations

import math
from datetime import date

import pytest

from talent_screener.domain.contexts.document_intelligence import (
    BatchQuotaExceededError,
    BatchStatus,
    CandidateMatchedToJobEvent,
    CandidateProfile,
    CandidateProfileNormalizedEvent,
    CandidateRejectedByGatekeeperEvent,
    CandidateShortlistedForInterviewEvent,
    CandidateTier,
    ChunkEvidenceSample,
    CompetencyEvaluationSpec,
    DocumentExtractionFailedEvent,
    DocumentExtractionSucceededEvent,
    DocumentIngestionTask,
    DuplicateFileInBatchError,
    ExtractedSkillClaim,
    FileContentHash,
    HybridMatchingDomainService,
    HybridMatchVector,
    ImpactMetric,
    InvalidDateRangeError,
    InvalidMatchWeightsError,
    MatchThresholds,
    PiiRedactionMap,
    ProjectComplexityRecord,
    RecencyDecayFactor,
    ScreeningBatch,
    ScreeningBatchCompletedEvent,
    ScreeningBatchCreatedEvent,
    SeniorityEstimate,
    TaskIngestionStatus,
    UngroundedClaimError,
    WorkExperienceEntry,
)
from talent_screener.domain.shared import (
    ConfidenceScore,
    InvalidStateTransitionError,
    InvariantViolationError,
    SHA256Hash,
    TenantId,
    VersionNumber,
    generate_uuid7,
)


def _make_file_hash(
    seed: bytes, mime: str = "application/pdf", size: int = 2048
) -> FileContentHash:
    return FileContentHash(
        sha256=SHA256Hash.from_bytes(seed),
        file_size_bytes=size,
        mime_type=mime,
    )


def _sample_profile(
    tenant_id: TenantId,
    *,
    source_text: str | None = None,
    python_months: int = 48,
    include_k8s: bool = True,
) -> CandidateProfile:
    claims = [
        ExtractedSkillClaim(
            canonical_skill_id="SKILL_LANG_PYTHON",
            raw_label="Python",
            source_quote="Built high-throughput Python asyncio microservices handling 10k RPS.",
            claimed_depth=4,
            applied_months=python_months,
            recency_months=0,
            confidence=ConfidenceScore(0.95),
        )
    ]
    if include_k8s:
        claims.append(
            ExtractedSkillClaim(
                canonical_skill_id="SKILL_KUBERNETES",
                raw_label="Kubernetes",
                source_quote="Operated multi-AZ Kubernetes clusters with KEDA autoscaling.",
                claimed_depth=3,
                applied_months=24,
                recency_months=6,
                confidence=ConfidenceScore(0.90),
            )
        )
    work = [
        WorkExperienceEntry(
            role_title="Senior Backend Engineer",
            normalized_role="BACKEND_ENGINEER",
            company_token="COMPANY_ALPHA",
            start_year=2023,
            start_month=1,
            end_year=2024,
            end_month=12,
        ),
        # Overlaps 2023-07..2024-12 (18 months) + 2022-07..2022-12 (6 months) -> union = 30 mos
        WorkExperienceEntry(
            role_title="Cloud Consultant",
            normalized_role="CLOUD_ARCHITECT",
            company_token="COMPANY_BETA",
            start_year=2022,
            start_month=7,
            end_year=2024,
            end_month=6,
        ),
    ]
    projects = [
        ProjectComplexityRecord(
            project_name="Async Ingestion Pipeline",
            scale_tier="HIGH_THROUGHPUT_PRODUCTION",
            summary="Processed 500 PDFs under 180s SLA.",
            source_quote="Architected distributed CV parser achieving 500 PDFs in 140s.",
            impact_metrics=(
                ImpactMetric(
                    metric_type="LATENCY_REDUCTION",
                    numeric_value=42.0,
                    unit="PERCENT",
                    source_quote="Reduced P95 ingestion latency by 42%.",
                ),
            ),
            linked_work_entry_id=work[0].id,
        )
    ]
    return CandidateProfile(
        tenant_id=tenant_id,
        anonymized_token=generate_uuid7(),
        file_hash=_make_file_hash(b"cv-binary-bytes"),
        pii_redaction_map=PiiRedactionMap.from_mapping(
            vault_reference_id=generate_uuid7(),
            token_categories={"<CANDIDATE_NAME_1>": "PERSON_NAME"},
        ),
        work_experiences=work,
        projects=projects,
        skill_claims=claims,
        seniority_estimate=SeniorityEstimate(
            level="SENIOR",
            verified_experience_months=30,
            confidence=ConfidenceScore(0.92),
        ),
        source_document_text=source_text,
    )


@pytest.mark.unit
def test_file_content_hash_and_screening_batch_invariants() -> None:
    tid = TenantId.generate()
    req_id = generate_uuid7()

    # Unsupported MIME rejected
    with pytest.raises(InvariantViolationError) as exc_mime:
        _make_file_hash(b"x", mime="text/plain")
    assert exc_mime.value.code == "UNSUPPORTED_MIME_TYPE"

    # Duplicate SHA-256 inside same batch rejected
    dup_hash = _make_file_hash(b"identical-cv")
    with pytest.raises(DuplicateFileInBatchError):
        ScreeningBatch(
            tenant_id=tid,
            requisition_id=req_id,
            batch_label="batch-dup",
            tasks=[
                DocumentIngestionTask(filename="a.pdf", file_hash=dup_hash),
                DocumentIngestionTask(filename="b.pdf", file_hash=dup_hash),
            ],
        )

    # Batch size > 500 rejected
    with pytest.raises(BatchQuotaExceededError):
        ScreeningBatch(
            tenant_id=tid,
            requisition_id=req_id,
            batch_label="batch-huge",
            tasks=[
                DocumentIngestionTask(
                    filename=f"f{i}.pdf",
                    file_hash=_make_file_hash(f"{i}".encode()),
                )
                for i in range(501)
            ],
        )

    t1 = DocumentIngestionTask(filename="c1.pdf", file_hash=_make_file_hash(b"c1"))
    t2 = DocumentIngestionTask(filename="c2.pdf", file_hash=_make_file_hash(b"c2"))
    batch = ScreeningBatch(
        tenant_id=tid,
        requisition_id=req_id,
        batch_label="spring-cohort",
        tasks=[t1, t2],
    )
    assert batch.total_files == 2
    assert batch.pending_count == 2
    assert batch.completed_count == 0
    assert batch.failed_count == 0
    created_events = batch.pull_domain_events()
    assert len(created_events) == 1
    assert isinstance(created_events[0], ScreeningBatchCreatedEvent)

    batch.queue()
    batch.begin_task_stage(t1.id, TaskIngestionStatus.PARSING)
    assert batch.status.value == BatchStatus.PROCESSING.value

    batch.mark_task_succeeded(t1.id, generate_uuid7())
    assert batch.completed_count == 1
    assert batch.pending_count == 1
    assert batch.total_files == batch.completed_count + batch.failed_count + batch.pending_count

    batch.mark_task_failed(
        t2.id, error_code="CORRUPT_PDF", error_message="Unreadable EOF", quarantined=True
    )
    assert batch.status.value == BatchStatus.PARTIALLY_FAILED.value
    assert batch.failed_count == 1
    assert batch.pending_count == 0
    assert batch.total_files == batch.completed_count + batch.failed_count + batch.pending_count

    events = batch.pull_domain_events()
    assert any(isinstance(e, DocumentExtractionSucceededEvent) for e in events)
    assert any(isinstance(e, DocumentExtractionFailedEvent) for e in events)
    assert any(isinstance(e, ScreeningBatchCompletedEvent) for e in events)

    with pytest.raises(InvalidStateTransitionError):
        batch.queue()


@pytest.mark.unit
def test_candidate_profile_grounding_and_non_overlapping_experience() -> None:
    tid = TenantId.generate()

    # Empty source_quote rejected immediately on ExtractedSkillClaim
    with pytest.raises(UngroundedClaimError):
        ExtractedSkillClaim(
            canonical_skill_id="SKILL_RUST",
            raw_label="Rust",
            source_quote="   ",
        )

    # Negative date range rejected on WorkExperienceEntry
    with pytest.raises(InvalidDateRangeError):
        WorkExperienceEntry(
            role_title="Dev",
            normalized_role="BACKEND",
            company_token="COMP",
            start_year=2024,
            start_month=6,
            end_year=2024,
            end_month=1,
        )

    # Ongoing role with end_year rejected
    with pytest.raises(InvalidDateRangeError):
        WorkExperienceEntry(
            role_title="Dev",
            normalized_role="BACKEND",
            company_token="COMP",
            start_year=2024,
            start_month=6,
            end_year=2025,
            end_month=1,
            is_current=True,
        )

    # Source text lacking verbatim claim fails grounding invariant
    with pytest.raises(UngroundedClaimError):
        _sample_profile(tid, source_text="Only mentions Java and Spring Boot experience.")

    # Full matching source text passes and deduplicates overlapping intervals
    valid_text = (
        "Senior Backend Engineer. Built high-throughput Python asyncio microservices handling "
        "10k RPS. Operated multi-AZ Kubernetes clusters with KEDA autoscaling. "
        "Architected distributed CV parser achieving 500 PDFs in 140s."
    )
    profile = _sample_profile(tid, source_text=valid_text)
    assert profile.compute_total_verified_experience_months(as_of=date(2025, 1, 1)) == 30
    events = profile.pull_domain_events()
    assert len(events) == 1
    assert isinstance(events[0], CandidateProfileNormalizedEvent)


@pytest.mark.unit
def test_hybrid_match_vector_recency_decay_and_gatekeeper_math() -> None:
    decay = RecencyDecayFactor.from_half_life_years(3.0)
    assert decay.decay_for_months(0) == pytest.approx(1.0)
    assert decay.decay_for_months(36) == pytest.approx(0.5, rel=1e-6)

    # Invalid convex combination weights fail
    with pytest.raises(InvalidMatchWeightsError):
        HybridMatchVector(
            dense_similarity=0.8,
            sparse_bm25_score=0.7,
            alpha_dense=0.5,
            beta_sparse=0.3,
            gamma_recency=0.1,
        )

    # Non-binary gatekeeper multiplier fails
    with pytest.raises(InvariantViolationError):
        HybridMatchVector(
            dense_similarity=0.8,
            sparse_bm25_score=0.7,
            gatekeeper_multiplier=0.5,
        )

    # Exact weighted convex calculation
    vec = HybridMatchVector(
        dense_similarity=0.90,
        sparse_bm25_score=0.80,
        experience_alignment=1.0,
        alpha_dense=0.55,
        beta_sparse=0.25,
        gamma_recency=0.20,
        gatekeeper_multiplier=1.0,
        recency_decay=decay,
        recency_months_elapsed=36,
    )
    expected_raw = 0.55 * 0.90 + 0.25 * 0.80 + 0.20 * (1.0 * math.exp(-decay.lambda_rate * 3.0))
    assert vec.raw_convex_score == pytest.approx(expected_raw, rel=1e-6)
    assert vec.composite_score == pytest.approx(100.0 * expected_raw, rel=1e-6)

    # Gatekeeper zeroing forces exact 0.0 score
    gated = HybridMatchVector(
        dense_similarity=1.0,
        sparse_bm25_score=1.0,
        experience_alignment=1.0,
        gatekeeper_multiplier=0.0,
    )
    assert gated.composite_score == 0.0


@pytest.mark.unit
def test_hybrid_matching_domain_service_scoring_tiers_and_events() -> None:
    tid = TenantId.generate()
    req_id = generate_uuid7()
    comp_py = generate_uuid7()
    comp_k8s = generate_uuid7()
    svc = HybridMatchingDomainService()

    competencies = [
        CompetencyEvaluationSpec(
            competency_id=comp_py,
            canonical_skill_id="SKILL_LANG_PYTHON",
            weight=0.70,
            target_depth=4,
            is_gatekeeper=True,
            min_applied_months=36,
            strict_gatekeeper=True,
        ),
        CompetencyEvaluationSpec(
            competency_id=comp_k8s,
            canonical_skill_id="SKILL_KUBERNETES",
            weight=0.30,
            target_depth=3,
            is_gatekeeper=False,
        ),
    ]

    # Case 1: Strong shortlisted candidate (FAST_TRACK)
    strong_profile = _sample_profile(tid, python_months=48, include_k8s=True)
    result_fast = svc.score_candidate(
        tenant_id=tid,
        requisition_id=req_id,
        rubric_version=VersionNumber(1),
        candidate_profile=strong_profile,
        competencies=competencies,
        evidence_by_competency={
            comp_py: [
                ChunkEvidenceSample(
                    dense_similarity=0.96,
                    sparse_bm25_score=0.92,
                    recency_months_delta=0,
                    evidentiary_depth=4,
                    verbatim_excerpt="Built high-throughput Python asyncio microservices.",
                ),
                ChunkEvidenceSample(
                    dense_similarity=0.90,
                    sparse_bm25_score=0.88,
                    recency_months_delta=0,
                    evidentiary_depth=4,
                ),
            ],
            comp_k8s: [
                ChunkEvidenceSample(
                    dense_similarity=0.92,
                    sparse_bm25_score=0.90,
                    recency_months_delta=0,
                    evidentiary_depth=4,
                ),
            ],
        },
        thresholds=MatchThresholds(88.0, 75.0, 58.0),
    )
    assert result_fast.tier is CandidateTier.FAST_TRACK
    assert result_fast.composite_score >= 88.0
    events_fast = result_fast.pull_domain_events()
    assert any(isinstance(e, CandidateMatchedToJobEvent) for e in events_fast)
    assert any(isinstance(e, CandidateShortlistedForInterviewEvent) for e in events_fast)

    # Case 2: Candidate failing gatekeeper (applied_months < 36) -> score 0.0 & GATEKEEPER_REJECTED
    junior_profile = _sample_profile(tid, python_months=12, include_k8s=True)
    result_rejected = svc.score_candidate(
        tenant_id=tid,
        requisition_id=req_id,
        rubric_version=VersionNumber(1),
        candidate_profile=junior_profile,
        competencies=competencies,
        evidence_by_competency={
            comp_py: [
                ChunkEvidenceSample(
                    dense_similarity=0.99,
                    sparse_bm25_score=0.99,
                    recency_months_delta=0,
                    evidentiary_depth=4,
                ),
            ],
            comp_k8s: [
                ChunkEvidenceSample(
                    dense_similarity=0.99,
                    sparse_bm25_score=0.99,
                    recency_months_delta=0,
                    evidentiary_depth=4,
                ),
            ],
        },
    )
    assert result_rejected.composite_score == 0.0
    assert result_rejected.tier is CandidateTier.GATEKEEPER_REJECTED
    assert "SKILL_LANG_PYTHON" in result_rejected.failed_gatekeepers
    events_rej = result_rejected.pull_domain_events()
    assert any(isinstance(e, CandidateRejectedByGatekeeperEvent) for e in events_rej)
