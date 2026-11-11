"""Document Intelligence Bounded Context Entities."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.exceptions import (
    InvalidDateRangeError,
    UngroundedClaimError,
)
from talent_screener.domain.contexts.document_intelligence.value_objects import (
    FileContentHash,
    ImpactMetric,
    TaskIngestionStatus,
)
from talent_screener.domain.shared.base import Entity
from talent_screener.domain.shared.exceptions import (
    InvalidStateTransitionError,
    InvariantViolationError,
)
from talent_screener.domain.shared.guards import (
    require_int_range,
    require_non_empty_str,
    require_non_nil_uuid,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now

_TERMINAL_TASK_STATUSES: frozenset[TaskIngestionStatus] = frozenset(
    {
        TaskIngestionStatus.COMPLETED,
        TaskIngestionStatus.FAILED,
        TaskIngestionStatus.QUARANTINED,
        TaskIngestionStatus.DUPLICATE_SKIPPED,
    }
)


class DocumentIngestionTask(Entity[UUID]):
    """Child entity tracking the ingestion lifecycle of a single CV inside a ScreeningBatch."""

    __slots__ = (
        "_candidate_profile_id",
        "_error_code",
        "_error_message",
        "_file_hash",
        "_filename",
        "_retry_count",
        "_status",
    )

    def __init__(
        self,
        *,
        filename: str,
        file_hash: FileContentHash,
        task_id: UUID | None = None,
        status: TaskIngestionStatus = TaskIngestionStatus.PENDING,
    ) -> None:
        resolved_id = task_id if task_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "task_id", code="INVALID_TASK_ID")
        super().__init__(resolved_id)

        self._filename: str = require_non_empty_str(
            filename,
            "filename",
            max_len=255,
            code="INVALID_FILENAME",
        )
        if not isinstance(file_hash, FileContentHash):
            raise InvariantViolationError(
                "file_hash must be a FileContentHash.",
                code="INVALID_FILE_HASH",
            )
        self._file_hash: FileContentHash = file_hash
        self._status: TaskIngestionStatus = status
        self._candidate_profile_id: UUID | None = None
        self._error_code: str | None = None
        self._error_message: str | None = None
        self._retry_count: int = 0

    @property
    def filename(self) -> str:
        return self._filename

    @property
    def file_hash(self) -> FileContentHash:
        return self._file_hash

    @property
    def status(self) -> TaskIngestionStatus:
        return self._status

    @property
    def candidate_profile_id(self) -> UUID | None:
        return self._candidate_profile_id

    @property
    def error_code(self) -> str | None:
        return self._error_code

    @property
    def error_message(self) -> str | None:
        return self._error_message

    @property
    def retry_count(self) -> int:
        return self._retry_count

    @property
    def is_terminal(self) -> bool:
        return self._status in _TERMINAL_TASK_STATUSES

    def advance_stage(self, new_status: TaskIngestionStatus) -> None:
        """Advance task through intermediate ingestion stages."""
        if self.is_terminal:
            raise InvalidStateTransitionError(
                f"Cannot transition terminal task from {self._status} to {new_status}.",
                from_state=self._status.value,
                to_state=new_status.value,
                code="INVALID_TASK_TRANSITION",
            )
        self._status = new_status

    def mark_succeeded(self, candidate_profile_id: UUID) -> None:
        """Mark document ingestion as COMPLETED with the produced CandidateProfile ID."""
        if self.is_terminal:
            raise InvalidStateTransitionError(
                f"DocumentIngestionTask is already terminal ({self._status}).",
                from_state=self._status.value,
                to_state=TaskIngestionStatus.COMPLETED.value,
                code="INVALID_TASK_TRANSITION",
            )
        require_non_nil_uuid(
            candidate_profile_id, "candidate_profile_id", code="INVALID_PROFILE_ID"
        )
        self._candidate_profile_id = candidate_profile_id
        self._status = TaskIngestionStatus.COMPLETED

    def mark_failed(
        self,
        *,
        error_code: str,
        error_message: str,
        quarantined: bool = False,
    ) -> None:
        """Mark document ingestion as FAILED or QUARANTINED."""
        if self.is_terminal:
            raise InvalidStateTransitionError(
                f"DocumentIngestionTask is already terminal ({self._status}).",
                from_state=self._status.value,
                to_state=TaskIngestionStatus.FAILED.value,
                code="INVALID_TASK_TRANSITION",
            )
        self._error_code = require_non_empty_str(
            error_code,
            "error_code",
            max_len=64,
            code="INVALID_ERROR_CODE",
        )
        self._error_message = require_non_empty_str(
            error_message,
            "error_message",
            max_len=500,
            code="INVALID_ERROR_MESSAGE",
        )
        self._status = (
            TaskIngestionStatus.QUARANTINED if quarantined else TaskIngestionStatus.FAILED
        )


class WorkExperienceEntry(Entity[UUID]):
    """Chronological employment interval enforcing non-negative date ranges."""

    __slots__ = (
        "_company_token",
        "_employment_type",
        "_end_month",
        "_end_year",
        "_is_current",
        "_normalized_role",
        "_role_title",
        "_start_month",
        "_start_year",
    )

    def __init__(
        self,
        *,
        role_title: str,
        normalized_role: str,
        company_token: str,
        start_year: int,
        start_month: int,
        end_year: int | None = None,
        end_month: int | None = None,
        is_current: bool = False,
        employment_type: str = "FULL_TIME",
        entry_id: UUID | None = None,
    ) -> None:
        resolved_id = entry_id if entry_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "entry_id", code="INVALID_EXPERIENCE_ID")
        super().__init__(resolved_id)

        self._role_title = require_non_empty_str(
            role_title, "role_title", max_len=120, code="INVALID_ROLE_TITLE"
        )
        self._normalized_role = require_non_empty_str(
            normalized_role, "normalized_role", max_len=80, code="INVALID_NORMALIZED_ROLE"
        )
        self._company_token = require_non_empty_str(
            company_token, "company_token", max_len=120, code="INVALID_COMPANY_TOKEN"
        )
        self._employment_type = require_non_empty_str(
            employment_type, "employment_type", max_len=40, code="INVALID_EMPLOYMENT_TYPE"
        ).upper()

        self._start_year = require_int_range(
            start_year, "start_year", 1960, 2100, code="INVALID_DATE_RANGE"
        )
        self._start_month = require_int_range(
            start_month, "start_month", 1, 12, code="INVALID_DATE_RANGE"
        )

        if not isinstance(is_current, bool):
            raise InvalidDateRangeError("is_current must be a boolean.")
        self._is_current = is_current

        if is_current:
            if end_year is not None or end_month is not None:
                raise InvalidDateRangeError(
                    "Ongoing work experience (is_current=True) must not specify an end date.",
                )
            self._end_year = None
            self._end_month = None
        else:
            if end_year is None or end_month is None:
                raise InvalidDateRangeError(
                    "Completed work experience (is_current=False) requires end_year and end_month.",
                )
            validated_end_year = require_int_range(
                end_year, "end_year", 1960, 2100, code="INVALID_DATE_RANGE"
            )
            validated_end_month = require_int_range(
                end_month, "end_month", 1, 12, code="INVALID_DATE_RANGE"
            )
            if (validated_end_year, validated_end_month) < (self._start_year, self._start_month):
                raise InvalidDateRangeError(
                    "WorkExperienceEntry end date cannot precede start date "
                    f"({validated_end_year:04d}-{validated_end_month:02d} < "
                    f"{self._start_year:04d}-{self._start_month:02d}).",
                )
            self._end_year = validated_end_year
            self._end_month = validated_end_month

    @property
    def role_title(self) -> str:
        return self._role_title

    @property
    def normalized_role(self) -> str:
        return self._normalized_role

    @property
    def company_token(self) -> str:
        return self._company_token

    @property
    def employment_type(self) -> str:
        return self._employment_type

    @property
    def start_year(self) -> int:
        return self._start_year

    @property
    def start_month(self) -> int:
        return self._start_month

    @property
    def end_year(self) -> int | None:
        return self._end_year

    @property
    def end_month(self) -> int | None:
        return self._end_month

    @property
    def is_current(self) -> bool:
        return self._is_current

    def month_interval(self, *, as_of: date | None = None) -> tuple[int, int]:
        """Return inclusive-exclusive [start_month_index, end_month_index) tuple."""
        ref = as_of if as_of is not None else utc_now().date()
        start_idx = self._start_year * 12 + (self._start_month - 1)
        if self._is_current:
            end_idx = ref.year * 12 + (ref.month - 1) + 1
            if end_idx < start_idx:
                raise InvalidDateRangeError(
                    "Reference date precedes WorkExperienceEntry start date.",
                )
        else:
            assert self._end_year is not None and self._end_month is not None
            end_idx = self._end_year * 12 + (self._end_month - 1) + 1
        return (start_idx, end_idx)

    def duration_months(self, *, as_of: date | None = None) -> int:
        """Compute non-negative duration in calendar months."""
        start_idx, end_idx = self.month_interval(as_of=as_of)
        return max(0, end_idx - start_idx)


class ProjectComplexityRecord(Entity[UUID]):
    """Extracted technical project artifact grounded with verbatim source evidence."""

    __slots__ = (
        "_impact_metrics",
        "_linked_work_entry_id",
        "_project_name",
        "_scale_tier",
        "_source_quote",
        "_summary",
    )

    _VALID_SCALE_TIERS = frozenset(
        {
            "UNSPECIFIED",
            "PROTOTYPE_SINGLE_USER",
            "DEPARTMENTAL_INTERNAL",
            "HIGH_THROUGHPUT_PRODUCTION",
            "DISTRIBUTED_MISSION_CRITICAL",
        }
    )

    def __init__(
        self,
        *,
        project_name: str,
        scale_tier: str,
        summary: str,
        source_quote: str,
        impact_metrics: tuple[ImpactMetric, ...] = (),
        linked_work_entry_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> None:
        resolved_id = project_id if project_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "project_id", code="INVALID_PROJECT_ID")
        super().__init__(resolved_id)

        self._project_name = require_non_empty_str(
            project_name, "project_name", max_len=120, code="INVALID_PROJECT_NAME"
        )
        cleaned_scale = require_non_empty_str(
            scale_tier, "scale_tier", max_len=64, code="INVALID_SCALE_TIER"
        ).upper()
        if cleaned_scale not in self._VALID_SCALE_TIERS:
            raise InvariantViolationError(
                f"Invalid project scale_tier: {cleaned_scale!r}.",
                code="INVALID_SCALE_TIER",
            )
        self._scale_tier = cleaned_scale
        self._summary = require_non_empty_str(
            summary, "summary", max_len=600, code="INVALID_PROJECT_SUMMARY"
        )
        if not isinstance(source_quote, str) or len(source_quote.strip()) < 3:
            raise UngroundedClaimError(
                "ProjectComplexityRecord requires a non-empty verbatim source_quote.",
            )
        self._source_quote = source_quote.strip()
        self._impact_metrics = tuple(impact_metrics)
        if linked_work_entry_id is not None:
            require_non_nil_uuid(
                linked_work_entry_id,
                "linked_work_entry_id",
                code="INVALID_EXPERIENCE_ID",
            )
        self._linked_work_entry_id = linked_work_entry_id

    @property
    def project_name(self) -> str:
        return self._project_name

    @property
    def scale_tier(self) -> str:
        return self._scale_tier

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def source_quote(self) -> str:
        return self._source_quote

    @property
    def impact_metrics(self) -> tuple[ImpactMetric, ...]:
        return self._impact_metrics

    @property
    def linked_work_entry_id(self) -> UUID | None:
        return self._linked_work_entry_id
