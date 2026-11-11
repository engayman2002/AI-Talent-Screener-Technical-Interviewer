"""Document Intelligence Bounded Context Aggregate Roots: ScreeningBatch & CandidateProfile."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from uuid import UUID

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
    UngroundedClaimError,
)
from talent_screener.domain.contexts.document_intelligence.value_objects import (
    BatchStatus,
    ExtractedSkillClaim,
    FileContentHash,
    PiiRedactionMap,
    SeniorityEstimate,
    TaskIngestionStatus,
)
from talent_screener.domain.shared.base import AggregateRoot
from talent_screener.domain.shared.exceptions import (
    EntityNotFoundError,
    InvalidStateTransitionError,
    InvariantViolationError,
)
from talent_screener.domain.shared.guards import require_non_empty_str, require_non_nil_uuid
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import TenantId, VersionNumber

MAX_FILES_PER_BATCH: int = 500

_ALLOWED_BATCH_TRANSITIONS: dict[BatchStatus, frozenset[BatchStatus]] = {
    BatchStatus.INITIATED: frozenset({BatchStatus.QUEUED, BatchStatus.EXPIRED}),
    BatchStatus.QUEUED: frozenset({BatchStatus.PROCESSING, BatchStatus.EXPIRED}),
    BatchStatus.PROCESSING: frozenset(
        {
            BatchStatus.COMPLETED,
            BatchStatus.PARTIALLY_FAILED,
            BatchStatus.FAILED,
        }
    ),
    BatchStatus.COMPLETED: frozenset(),
    BatchStatus.PARTIALLY_FAILED: frozenset(),
    BatchStatus.FAILED: frozenset(),
    BatchStatus.EXPIRED: frozenset(),
}

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.strip()).lower()


def _is_quote_grounded(quote: str, source_text: str) -> bool:
    """Verify verbatim substring grounding (or >= 0.90 contiguous/token overlap)."""
    norm_quote = _normalize_whitespace(quote)
    norm_source = _normalize_whitespace(source_text)
    if not norm_quote:
        return False
    if norm_quote in norm_source:
        return True
    quote_tokens = norm_quote.split(" ")
    source_tokens = set(norm_source.split(" "))
    if not quote_tokens:
        return False
    overlap = sum(1 for tok in quote_tokens if tok in source_tokens) / len(quote_tokens)
    return overlap >= 0.90


class ScreeningBatch(AggregateRoot[UUID]):
    """Transactional batch of uploaded candidate CVs enforcing count and dedup invariants."""

    __slots__ = (
        "_batch_label",
        "_completed_count",
        "_failed_count",
        "_pending_count",
        "_requisition_id",
        "_seen_hashes",
        "_status",
        "_tasks",
        "_total_files",
    )

    def __init__(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        batch_label: str,
        tasks: Sequence[DocumentIngestionTask],
        batch_id: UUID | None = None,
        version: VersionNumber | None = None,
        emit_created_event: bool = True,
    ) -> None:
        resolved_id = batch_id if batch_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "batch_id", code="INVALID_BATCH_ID")
        require_non_nil_uuid(requisition_id, "requisition_id", code="INVALID_REQUISITION_ID")
        super().__init__(resolved_id, tenant_id=tenant_id, version=version)

        self._requisition_id: UUID = requisition_id
        self._batch_label: str = require_non_empty_str(
            batch_label,
            "batch_label",
            max_len=120,
            code="INVALID_BATCH_LABEL",
        )

        task_list = list(tasks)
        if not task_list or len(task_list) > MAX_FILES_PER_BATCH:
            raise BatchQuotaExceededError(
                f"ScreeningBatch must contain between 1 and {MAX_FILES_PER_BATCH} files; "
                f"got {len(task_list)}.",
            )

        seen_task_ids: set[UUID] = set()
        seen_hashes: set[str] = set()
        task_map: dict[UUID, DocumentIngestionTask] = {}
        for task in task_list:
            if task.id in seen_task_ids:
                raise InvariantViolationError(
                    f"Duplicate task_id {task.id} in ScreeningBatch.",
                    code="DUPLICATE_TASK_ID",
                )
            seen_task_ids.add(task.id)
            digest = task.file_hash.sha256.value
            if digest in seen_hashes:
                raise DuplicateFileInBatchError(
                    f"Duplicate file content SHA-256 {digest} detected in ScreeningBatch.",
                )
            seen_hashes.add(digest)
            task_map[task.id] = task

        self._tasks: dict[UUID, DocumentIngestionTask] = task_map
        self._seen_hashes: set[str] = seen_hashes
        self._total_files: int = len(task_map)
        self._status: BatchStatus = BatchStatus.INITIATED
        self._completed_count: int = 0
        self._failed_count: int = 0
        self._pending_count: int = 0
        self._recompute_and_verify_counters()

        if emit_created_event:
            self._record_event(
                ScreeningBatchCreatedEvent(
                    aggregate_id=self._id,
                    tenant_id=self._tenant_id,
                    batch_id=self._id,
                    requisition_id=self._requisition_id,
                    total_files=self._total_files,
                )
            )

    @property
    def requisition_id(self) -> UUID:
        return self._requisition_id

    @property
    def batch_label(self) -> str:
        return self._batch_label

    @property
    def status(self) -> BatchStatus:
        return self._status

    @property
    def total_files(self) -> int:
        return self._total_files

    @property
    def completed_count(self) -> int:
        return self._completed_count

    @property
    def failed_count(self) -> int:
        return self._failed_count

    @property
    def pending_count(self) -> int:
        return self._pending_count

    @property
    def tasks(self) -> tuple[DocumentIngestionTask, ...]:
        return tuple(self._tasks.values())

    @property
    def content_hashes(self) -> frozenset[str]:
        return frozenset(self._seen_hashes)

    def _recompute_and_verify_counters(self) -> None:
        completed = 0
        failed = 0
        pending = 0
        for task in self._tasks.values():
            if task.status is TaskIngestionStatus.COMPLETED:
                completed += 1
            elif task.status in (
                TaskIngestionStatus.FAILED,
                TaskIngestionStatus.QUARANTINED,
                TaskIngestionStatus.DUPLICATE_SKIPPED,
            ):
                failed += 1
            else:
                pending += 1

        if completed + failed + pending != self._total_files:
            raise BatchCountMismatchError(
                "ScreeningBatch count invariant violated: "
                f"total({self._total_files}) != completed({completed}) + failed({failed}) + "
                f"pending({pending}).",
            )
        self._completed_count = completed
        self._failed_count = failed
        self._pending_count = pending

    def _transition_status(self, target: BatchStatus) -> None:
        allowed = _ALLOWED_BATCH_TRANSITIONS[self._status]
        if target not in allowed:
            raise InvalidStateTransitionError(
                f"Cannot transition ScreeningBatch from {self._status} to {target}.",
                from_state=self._status.value,
                to_state=target.value,
                code="INVALID_BATCH_TRANSITION",
            )
        self._status = target
        self._increment_version()

    def queue(self) -> None:
        """Transition batch from INITIATED to QUEUED."""
        self._transition_status(BatchStatus.QUEUED)

    def expire(self) -> None:
        """Expire an un-started batch."""
        self._transition_status(BatchStatus.EXPIRED)

    def start_processing(self) -> None:
        """Transition batch from QUEUED to PROCESSING."""
        self._transition_status(BatchStatus.PROCESSING)

    def _require_task(self, task_id: UUID) -> DocumentIngestionTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise EntityNotFoundError(
                f"DocumentIngestionTask {task_id} not found in batch {self._id}.",
                code="TASK_NOT_FOUND",
            )
        return task

    def _ensure_processing_state(self) -> None:
        if self._status is BatchStatus.QUEUED:
            self._transition_status(BatchStatus.PROCESSING)
        elif self._status is not BatchStatus.PROCESSING:
            raise InvalidStateTransitionError(
                f"Batch must be QUEUED or PROCESSING to mutate tasks; got {self._status}.",
                from_state=self._status.value,
                code="INVALID_BATCH_TRANSITION",
            )

    def begin_task_stage(self, task_id: UUID, stage: TaskIngestionStatus) -> None:
        """Advance an active task to a non-terminal processing stage."""
        self._ensure_processing_state()
        task = self._require_task(task_id)
        task.advance_stage(stage)
        self._recompute_and_verify_counters()

    def mark_task_succeeded(self, task_id: UUID, candidate_profile_id: UUID) -> None:
        """Record successful CV extraction and advance batch completion if all tasks finished."""
        self._ensure_processing_state()
        task = self._require_task(task_id)
        task.mark_succeeded(candidate_profile_id)
        self._recompute_and_verify_counters()
        self._increment_version()
        self._record_event(
            DocumentExtractionSucceededEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                batch_id=self._id,
                task_id=task.id,
                candidate_profile_id=candidate_profile_id,
                content_sha256=task.file_hash.sha256.value,
            )
        )
        self._check_terminal_completion()

    def mark_task_failed(
        self,
        task_id: UUID,
        *,
        error_code: str,
        error_message: str,
        quarantined: bool = False,
    ) -> None:
        """Record failed or quarantined CV ingestion and advance batch completion if finished."""
        self._ensure_processing_state()
        task = self._require_task(task_id)
        task.mark_failed(
            error_code=error_code,
            error_message=error_message,
            quarantined=quarantined,
        )
        self._recompute_and_verify_counters()
        self._increment_version()
        self._record_event(
            DocumentExtractionFailedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                batch_id=self._id,
                task_id=task.id,
                error_code=error_code,
                quarantined=quarantined,
            )
        )
        self._check_terminal_completion()

    def _check_terminal_completion(self) -> None:
        if self._pending_count > 0:
            return
        if self._failed_count == 0:
            target = BatchStatus.COMPLETED
        elif self._completed_count > 0:
            target = BatchStatus.PARTIALLY_FAILED
        else:
            target = BatchStatus.FAILED
        self._transition_status(target)
        self._record_event(
            ScreeningBatchCompletedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                batch_id=self._id,
                final_status=self._status.value,
                completed_count=self._completed_count,
                failed_count=self._failed_count,
            )
        )


class CandidateProfile(AggregateRoot[UUID]):
    """Zero-PII canonical candidate profile grounded by verbatim source quotes."""

    __slots__ = (
        "_anonymized_token",
        "_file_hash",
        "_pii_redaction_map",
        "_projects",
        "_seniority_estimate",
        "_skill_claims",
        "_work_experiences",
    )

    def __init__(
        self,
        *,
        tenant_id: TenantId,
        anonymized_token: UUID,
        file_hash: FileContentHash,
        pii_redaction_map: PiiRedactionMap,
        work_experiences: Sequence[WorkExperienceEntry],
        projects: Sequence[ProjectComplexityRecord],
        skill_claims: Sequence[ExtractedSkillClaim],
        seniority_estimate: SeniorityEstimate,
        source_document_text: str | None = None,
        profile_id: UUID | None = None,
        version: VersionNumber | None = None,
        emit_normalized_event: bool = True,
    ) -> None:
        resolved_id = profile_id if profile_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "profile_id", code="INVALID_PROFILE_ID")
        require_non_nil_uuid(anonymized_token, "anonymized_token", code="INVALID_CANDIDATE_TOKEN")
        super().__init__(resolved_id, tenant_id=tenant_id, version=version)

        if not isinstance(file_hash, FileContentHash):
            raise InvariantViolationError(
                "file_hash must be a FileContentHash.", code="INVALID_FILE_HASH"
            )
        if not isinstance(pii_redaction_map, PiiRedactionMap):
            raise InvariantViolationError(
                "pii_redaction_map must be a PiiRedactionMap.",
                code="INVALID_PII_REDACTION_MAP",
            )
        if not isinstance(seniority_estimate, SeniorityEstimate):
            raise InvariantViolationError(
                "seniority_estimate must be a SeniorityEstimate.",
                code="INVALID_SENIORITY_ESTIMATE",
            )

        claims_tuple = tuple(skill_claims)
        if not claims_tuple:
            raise InvariantViolationError(
                "CandidateProfile must contain at least one ExtractedSkillClaim.",
                code="EMPTY_SKILL_CLAIMS",
            )
        projects_tuple = tuple(projects)
        work_tuple = tuple(work_experiences)

        if source_document_text is not None:
            cleaned_source = require_non_empty_str(
                source_document_text,
                "source_document_text",
                min_len=10,
                code="INVALID_SOURCE_DOCUMENT_TEXT",
            )
            for claim in claims_tuple:
                if not _is_quote_grounded(claim.source_quote, cleaned_source):
                    raise UngroundedClaimError(
                        f"Skill claim {claim.canonical_skill_id!r} failed verbatim grounding.",
                    )
            for project in projects_tuple:
                if not _is_quote_grounded(project.source_quote, cleaned_source):
                    raise UngroundedClaimError(
                        f"Project {project.project_name!r} failed verbatim grounding check.",
                    )

        self._anonymized_token: UUID = anonymized_token
        self._file_hash: FileContentHash = file_hash
        self._pii_redaction_map: PiiRedactionMap = pii_redaction_map
        self._work_experiences: tuple[WorkExperienceEntry, ...] = work_tuple
        self._projects: tuple[ProjectComplexityRecord, ...] = projects_tuple
        self._skill_claims: tuple[ExtractedSkillClaim, ...] = claims_tuple
        self._seniority_estimate: SeniorityEstimate = seniority_estimate

        if emit_normalized_event:
            self._record_event(
                CandidateProfileNormalizedEvent(
                    aggregate_id=self._id,
                    tenant_id=self._tenant_id,
                    candidate_profile_id=self._id,
                    anonymized_token=self._anonymized_token,
                    verified_experience_months=self.compute_total_verified_experience_months(),
                    skill_count=len(self._skill_claims),
                )
            )

    @property
    def anonymized_token(self) -> UUID:
        return self._anonymized_token

    @property
    def file_hash(self) -> FileContentHash:
        return self._file_hash

    @property
    def pii_redaction_map(self) -> PiiRedactionMap:
        return self._pii_redaction_map

    @property
    def work_experiences(self) -> tuple[WorkExperienceEntry, ...]:
        return self._work_experiences

    @property
    def projects(self) -> tuple[ProjectComplexityRecord, ...]:
        return self._projects

    @property
    def skill_claims(self) -> tuple[ExtractedSkillClaim, ...]:
        return self._skill_claims

    @property
    def seniority_estimate(self) -> SeniorityEstimate:
        return self._seniority_estimate

    def compute_total_verified_experience_months(self, *, as_of: date | None = None) -> int:
        """Compute union of non-overlapping work experience intervals in months (INV-DI-03)."""
        if not self._work_experiences:
            return 0
        intervals = sorted(
            entry.month_interval(as_of=as_of) for entry in self._work_experiences
        )
        merged: list[tuple[int, int]] = [intervals[0]]
        for start, end in intervals[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return sum(end - start for start, end in merged)
