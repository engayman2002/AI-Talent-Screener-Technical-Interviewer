"""ACID Relational Repository Adapters implementing Domain & Application Repository Ports."""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID

from talent_screener.application.common.exceptions import (
    ResourceConflictError,
    UnauthorizedTenantAccessError,
)
from talent_screener.application.document_intelligence.dtos import CandidateProfileReadDTO
from talent_screener.domain.contexts.document_intelligence import (
    CandidateProfile,
    ScreeningBatch,
)
from talent_screener.domain.contexts.document_intelligence.matching import SemanticMatchResult
from talent_screener.domain.contexts.evaluation_analytics import EvaluationReport
from talent_screener.domain.contexts.identity_governance import (
    ConsentRecord,
    TenantAccount,
)
from talent_screener.domain.contexts.live_interview import InterviewSession
from talent_screener.domain.contexts.recruitment import (
    JobRequisition,
    RequisitionStatus,
)
from talent_screener.domain.shared.base import AggregateRoot
from talent_screener.domain.shared.value_objects import TenantId, VersionNumber
from talent_screener.infrastructure.persistence.relational.mappers import (
    candidate_profile_to_row,
    consent_record_to_row,
    evaluation_report_to_rows,
    interview_session_to_rows,
    job_requisition_to_rows,
    row_to_candidate_profile,
    row_to_candidate_profile_read_dto,
    row_to_consent_record,
    row_to_evaluation_report,
    row_to_interview_session,
    row_to_job_requisition,
    row_to_screening_batch,
    row_to_semantic_match,
    row_to_tenant_account,
    screening_batch_to_rows,
    semantic_match_to_row,
    tenant_account_to_row,
)
from talent_screener.infrastructure.persistence.relational.schema import SqlDatabaseManager

_MAX_TRACKED_INSTANCES = 16_384


def _require_tenant(tenant_id: object) -> TenantId:
    if not isinstance(tenant_id, TenantId) or not isinstance(tenant_id.value, UUID):
        raise UnauthorizedTenantAccessError(
            "Valid TenantId is required for relational repository access.",
            code="MISSING_TENANT_CONTEXT",
        )
    return tenant_id


class _VersionTracker:
    """Bounded per-DB registry of hydrated/saved aggregate OCC base versions."""

    def __init__(self) -> None:
        self._versions: OrderedDict[tuple[str, int, str], int] = OrderedDict()

    @staticmethod
    def _key(table: str, agg: AggregateRoot[Any]) -> tuple[str, int, str]:
        return (table, id(agg), str(agg.id))

    def get(self, table: str, agg: AggregateRoot[Any]) -> int | None:
        key = self._key(table, agg)
        val = self._versions.get(key)
        if val is not None:
            self._versions.move_to_end(key)
        return val

    def set(self, table: str, agg: AggregateRoot[Any], version: int) -> None:
        key = self._key(table, agg)
        self._versions[key] = version
        self._versions.move_to_end(key)
        while len(self._versions) > _MAX_TRACKED_INSTANCES:
            self._versions.popitem(last=False)

    def delete(self, table: str, agg: AggregateRoot[Any]) -> None:
        self._versions.pop(self._key(table, agg), None)


_TRACKERS_BY_DB: dict[int, _VersionTracker] = {}


def _tracker_for(db: SqlDatabaseManager) -> _VersionTracker:
    db_id = id(db)
    tracker = _TRACKERS_BY_DB.get(db_id)
    if tracker is None:
        tracker = _VersionTracker()
        _TRACKERS_BY_DB[db_id] = tracker
    return tracker


class BaseSqlRepository:
    """Shared OCC, tenant-isolation, and upsert helper for concrete SQL repositories."""

    def __init__(self, db: SqlDatabaseManager) -> None:
        self._db = db
        self._tracker = _tracker_for(db)

    def _record_hydrated[T: AggregateRoot[Any]](self, table: str, agg: T) -> T:
        self._tracker.set(table, agg, int(agg.version))
        return agg

    def _upsert_aggregate(
        self,
        *,
        aggregate: AggregateRoot[Any],
        table: str,
        pk_col: str,
        pk_val: str,
        columns: dict[str, object],
        after_children: Callable[[str], None] | None = None,
    ) -> None:
        tenant = _require_tenant(aggregate.tenant_id)
        tenant_str = str(tenant.value)
        tracker = self._tracker
        prev_tracked = tracker.get(table, aggregate)
        prev_agg_version = aggregate.version

        def _compensate() -> None:
            if prev_tracked is None:
                tracker.delete(table, aggregate)
            else:
                tracker.set(table, aggregate, prev_tracked)
            aggregate._version = prev_agg_version

        with self._db.write_transaction():
            existing = self._db.fetchone(
                f"SELECT version FROM {table} WHERE {pk_col} = ? AND tenant_id = ?",
                (pk_val, tenant_str),
            )
            if existing is None and prev_tracked is None:
                # Ensure pk does not belong to a different tenant before inserting.
                cross_tenant = self._db.fetchone(
                    f"SELECT tenant_id FROM {table} WHERE {pk_col} = ?",
                    (pk_val,),
                )
                if cross_tenant is not None:
                    raise ResourceConflictError(
                        f"Resource {table}({pk_val}) already exists under another tenant scope.",
                        code="CROSS_TENANT_CONFLICT",
                    )

                insert_cols = [pk_col, "tenant_id", *columns.keys(), "version"]
                placeholders = ", ".join("?" for _ in insert_cols)
                col_sql = ", ".join(insert_cols)
                initial_ver = int(aggregate.version)
                values: list[object] = [pk_val, tenant_str, *columns.values(), initial_ver]
                try:
                    self._db.execute(
                        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})",
                        values,
                    )
                except sqlite3.IntegrityError as exc:
                    raise ResourceConflictError(
                        f"Unique or optimistic conflict inserting {table}({pk_val}): {exc}",
                        code="OPTIMISTIC_CONCURRENCY_CONFLICT",
                        metadata={"table": table, "aggregate_id": pk_val},
                    ) from exc

                if after_children is not None:
                    after_children(tenant_str)
                tracker.set(table, aggregate, initial_ver)
                self._db.register_rollback_hook(_compensate)
                return

            if existing is None and prev_tracked is not None:
                raise ResourceConflictError(
                    f"Aggregate {table}({pk_val}) no longer exists in database.",
                    code="OPTIMISTIC_CONCURRENCY_CONFLICT",
                    metadata={"table": table, "aggregate_id": pk_val},
                )

            assert existing is not None
            expected_version = (
                prev_tracked
                if prev_tracked is not None
                else max(0, int(aggregate.version) - 1)
            )
            set_clauses = ", ".join(f"{col} = ?" for col in columns)
            update_sql = (
                f"UPDATE {table} SET {set_clauses}, version = version + 1 "
                f"WHERE {pk_col} = ? AND tenant_id = ? AND version = ?"
            )
            update_params: list[object] = [*columns.values(), pk_val, tenant_str, expected_version]
            try:
                cursor = self._db.execute(update_sql, update_params)
            except sqlite3.IntegrityError as exc:
                raise ResourceConflictError(
                    f"Integrity conflict updating {table}({pk_val}): {exc}",
                    code="OPTIMISTIC_CONCURRENCY_CONFLICT",
                    metadata={"table": table, "aggregate_id": pk_val},
                ) from exc

            if cursor.rowcount == 0:
                actual_version = int(existing["version"])
                raise ResourceConflictError(
                    f"Optimistic concurrency conflict on {table}({pk_val}): "
                    f"expected version {expected_version}, found {actual_version}.",
                    code="OPTIMISTIC_CONCURRENCY_CONFLICT",
                    metadata={
                        "table": table,
                        "aggregate_id": pk_val,
                        "expected_version": expected_version,
                        "actual_version": actual_version,
                    },
                )

            if after_children is not None:
                after_children(tenant_str)

            new_version = expected_version + 1
            aggregate._version = VersionNumber(new_version)
            tracker.set(table, aggregate, new_version)
            self._db.register_rollback_hook(_compensate)

    def _replace_children(
        self,
        *,
        child_table: str,
        fk_col: str,
        fk_val: str,
        tenant_str: str,
        child_rows: Sequence[dict[str, object]],
    ) -> None:
        self._db.execute(
            f"DELETE FROM {child_table} WHERE {fk_col} = ? AND tenant_id = ?",
            (fk_val, tenant_str),
        )
        if not child_rows:
            return
        cols = tuple(child_rows[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        col_sql = ", ".join(cols)
        self._db.executemany(
            f"INSERT INTO {child_table} ({col_sql}) VALUES ({placeholders})",
            [[row[c] for c in cols] for row in child_rows],
        )


class SqlJobRequisitionRepository(BaseSqlRepository):
    """SQL repository implementing JobRequisitionRepositoryPort."""

    _TABLE = "job_requisitions"

    def save(self, requisition: JobRequisition) -> None:
        parent_row, criteria_rows = job_requisition_to_rows(requisition)
        pk_val = str(parent_row.pop("requisition_id"))
        parent_row.pop("tenant_id")
        parent_row.pop("version")
        self._upsert_aggregate(
            aggregate=requisition,
            table=self._TABLE,
            pk_col="requisition_id",
            pk_val=pk_val,
            columns=parent_row,
            after_children=lambda tid: self._replace_children(
                child_table="competency_criteria",
                fk_col="requisition_id",
                fk_val=pk_val,
                tenant_str=tid,
                child_rows=criteria_rows,
            ),
        )

    def get_by_id(self, tenant_id: TenantId, requisition_id: UUID) -> JobRequisition | None:
        tenant = _require_tenant(tenant_id)
        t_str = str(tenant.value)
        req_str = str(requisition_id)
        row = self._db.fetchone(
            "SELECT * FROM job_requisitions WHERE requisition_id = ? AND tenant_id = ?",
            (req_str, t_str),
        )
        if row is None:
            return None
        c_rows = self._db.fetchall(
            "SELECT * FROM competency_criteria "
            "WHERE requisition_id = ? AND tenant_id = ? ORDER BY sort_order ASC",
            (req_str, t_str),
        )
        return self._record_hydrated(self._TABLE, row_to_job_requisition(row, c_rows))

    def list_by_tenant(
        self,
        tenant_id: TenantId,
        *,
        status: RequisitionStatus | None = None,
    ) -> tuple[JobRequisition, ...]:
        tenant = _require_tenant(tenant_id)
        t_str = str(tenant.value)
        if status is None:
            rows = self._db.fetchall(
                "SELECT * FROM job_requisitions WHERE tenant_id = ? ORDER BY requisition_id",
                (t_str,),
            )
        else:
            rows = self._db.fetchall(
                "SELECT * FROM job_requisitions "
                "WHERE tenant_id = ? AND status = ? ORDER BY requisition_id",
                (t_str, status.value),
            )
        results: list[JobRequisition] = []
        for row in rows:
            req_str = str(row["requisition_id"])
            c_rows = self._db.fetchall(
                "SELECT * FROM competency_criteria "
                "WHERE requisition_id = ? AND tenant_id = ? ORDER BY sort_order ASC",
                (req_str, t_str),
            )
            results.append(self._record_hydrated(self._TABLE, row_to_job_requisition(row, c_rows)))
        return tuple(results)


class SqlScreeningBatchRepository(BaseSqlRepository):
    """SQL repository implementing ScreeningBatchRepositoryPort."""

    _TABLE = "screening_batches"

    def save(self, batch: ScreeningBatch) -> None:
        parent_row, task_rows = screening_batch_to_rows(batch)
        pk_val = str(parent_row.pop("batch_id"))
        parent_row.pop("tenant_id")
        parent_row.pop("version")
        self._upsert_aggregate(
            aggregate=batch,
            table=self._TABLE,
            pk_col="batch_id",
            pk_val=pk_val,
            columns=parent_row,
            after_children=lambda tid: self._replace_children(
                child_table="document_ingestion_tasks",
                fk_col="batch_id",
                fk_val=pk_val,
                tenant_str=tid,
                child_rows=task_rows,
            ),
        )

    def get_by_id(self, tenant_id: TenantId, batch_id: UUID) -> ScreeningBatch | None:
        tenant = _require_tenant(tenant_id)
        t_str = str(tenant.value)
        batch_str = str(batch_id)
        row = self._db.fetchone(
            "SELECT * FROM screening_batches WHERE batch_id = ? AND tenant_id = ?",
            (batch_str, t_str),
        )
        if row is None:
            return None
        t_rows = self._db.fetchall(
            "SELECT * FROM document_ingestion_tasks "
            "WHERE batch_id = ? AND tenant_id = ? ORDER BY sort_order ASC",
            (batch_str, t_str),
        )
        return self._record_hydrated(self._TABLE, row_to_screening_batch(row, t_rows))


class SqlCandidateProfileRepository(BaseSqlRepository):
    """SQL repository implementing CandidateProfileRepositoryPort and ReadModelPort."""

    _TABLE = "candidate_profiles"

    def save(self, profile: CandidateProfile) -> None:
        row = candidate_profile_to_row(profile)
        pk_val = str(row.pop("profile_id"))
        row.pop("tenant_id")
        row.pop("version")
        self._upsert_aggregate(
            aggregate=profile,
            table=self._TABLE,
            pk_col="profile_id",
            pk_val=pk_val,
            columns=row,
        )

    def get_by_id(self, tenant_id: TenantId, profile_id: UUID) -> CandidateProfile | None:
        tenant = _require_tenant(tenant_id)
        row = self._db.fetchone(
            "SELECT * FROM candidate_profiles WHERE profile_id = ? AND tenant_id = ?",
            (str(profile_id), str(tenant.value)),
        )
        if row is None:
            return None
        return self._record_hydrated(self._TABLE, row_to_candidate_profile(row))

    def list_by_tenant(self, tenant_id: TenantId) -> tuple[CandidateProfile, ...]:
        """List all persisted candidate profiles belonging to ``tenant_id``."""
        tenant = _require_tenant(tenant_id)
        rows = self._db.fetchall(
            "SELECT * FROM candidate_profiles WHERE tenant_id = ? ORDER BY profile_id",
            (str(tenant.value),),
        )
        return tuple(
            self._record_hydrated(self._TABLE, row_to_candidate_profile(row)) for row in rows
        )

    async def get_profile_read_model(
        self,
        *,
        tenant_id: TenantId,
        profile_id: UUID,
    ) -> CandidateProfileReadDTO | None:
        tenant = _require_tenant(tenant_id)
        row = self._db.fetchone(
            "SELECT * FROM candidate_profiles WHERE profile_id = ? AND tenant_id = ?",
            (str(profile_id), str(tenant.value)),
        )
        if row is None:
            return None
        return row_to_candidate_profile_read_dto(row)


class SqlSemanticMatchResultRepository(BaseSqlRepository):
    """SQL repository implementing SemanticMatchResultRepositoryPort."""

    _TABLE = "semantic_match_results"

    def save(self, match_result: SemanticMatchResult) -> None:
        row = semantic_match_to_row(match_result)
        pk_val = str(row.pop("match_result_id"))
        row.pop("tenant_id")
        row.pop("version")
        self._upsert_aggregate(
            aggregate=match_result,
            table=self._TABLE,
            pk_col="match_result_id",
            pk_val=pk_val,
            columns=row,
        )

    def get_by_id(
        self, tenant_id: TenantId, match_result_id: UUID
    ) -> SemanticMatchResult | None:
        tenant = _require_tenant(tenant_id)
        row = self._db.fetchone(
            "SELECT * FROM semantic_match_results WHERE match_result_id = ? AND tenant_id = ?",
            (str(match_result_id), str(tenant.value)),
        )
        if row is None:
            return None
        return self._record_hydrated(self._TABLE, row_to_semantic_match(row))

    def get_by_candidate_and_requisition(
        self,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
    ) -> SemanticMatchResult | None:
        tenant = _require_tenant(tenant_id)
        row = self._db.fetchone(
            "SELECT * FROM semantic_match_results "
            "WHERE tenant_id = ? AND requisition_id = ? AND candidate_profile_id = ?",
            (str(tenant.value), str(requisition_id), str(candidate_profile_id)),
        )
        if row is None:
            return None
        return self._record_hydrated(self._TABLE, row_to_semantic_match(row))

    def list_by_requisition(
        self,
        tenant_id: TenantId,
        requisition_id: UUID,
    ) -> tuple[SemanticMatchResult, ...]:
        tenant = _require_tenant(tenant_id)
        rows = self._db.fetchall(
            "SELECT * FROM semantic_match_results "
            "WHERE tenant_id = ? AND requisition_id = ? ORDER BY match_result_id",
            (str(tenant.value), str(requisition_id)),
        )
        return tuple(
            self._record_hydrated(self._TABLE, row_to_semantic_match(row)) for row in rows
        )


class SqlInterviewSessionRepository(BaseSqlRepository):
    """SQL repository implementing InterviewSessionRepositoryPort."""

    _TABLE = "interview_sessions"

    def save(self, session: InterviewSession) -> None:
        parent_row, turn_rows = interview_session_to_rows(session)
        pk_val = str(parent_row.pop("session_id"))
        parent_row.pop("tenant_id")
        parent_row.pop("version")
        self._upsert_aggregate(
            aggregate=session,
            table=self._TABLE,
            pk_col="session_id",
            pk_val=pk_val,
            columns=parent_row,
            after_children=lambda tid: self._replace_children(
                child_table="interview_turns",
                fk_col="session_id",
                fk_val=pk_val,
                tenant_str=tid,
                child_rows=turn_rows,
            ),
        )

    def get_by_id(self, tenant_id: TenantId, session_id: UUID) -> InterviewSession | None:
        tenant = _require_tenant(tenant_id)
        t_str = str(tenant.value)
        s_str = str(session_id)
        row = self._db.fetchone(
            "SELECT * FROM interview_sessions WHERE session_id = ? AND tenant_id = ?",
            (s_str, t_str),
        )
        if row is None:
            return None
        turn_rows = self._db.fetchall(
            "SELECT * FROM interview_turns "
            "WHERE session_id = ? AND tenant_id = ? ORDER BY turn_number ASC",
            (s_str, t_str),
        )
        return self._record_hydrated(self._TABLE, row_to_interview_session(row, turn_rows))


class SqlEvaluationReportRepository(BaseSqlRepository):
    """SQL repository implementing EvaluationReportRepositoryPort."""

    _TABLE = "evaluation_reports"

    def save(self, report: EvaluationReport) -> None:
        parent_row, override_rows = evaluation_report_to_rows(report)
        pk_val = str(parent_row.pop("report_id"))
        parent_row.pop("tenant_id")
        parent_row.pop("version")
        self._upsert_aggregate(
            aggregate=report,
            table=self._TABLE,
            pk_col="report_id",
            pk_val=pk_val,
            columns=parent_row,
            after_children=lambda tid: self._replace_children(
                child_table="recruiter_override_audits",
                fk_col="report_id",
                fk_val=pk_val,
                tenant_str=tid,
                child_rows=override_rows,
            ),
        )

    def _hydrate_report(self, row: sqlite3.Row | None) -> EvaluationReport | None:
        if row is None:
            return None
        ov_rows = self._db.fetchall(
            "SELECT * FROM recruiter_override_audits "
            "WHERE report_id = ? AND tenant_id = ? ORDER BY sort_order ASC",
            (str(row["report_id"]), str(row["tenant_id"])),
        )
        return self._record_hydrated(self._TABLE, row_to_evaluation_report(row, ov_rows))

    def get_by_id(self, tenant_id: TenantId, report_id: UUID) -> EvaluationReport | None:
        tenant = _require_tenant(tenant_id)
        row = self._db.fetchone(
            "SELECT * FROM evaluation_reports WHERE report_id = ? AND tenant_id = ?",
            (str(report_id), str(tenant.value)),
        )
        return self._hydrate_report(row)

    def get_by_session_id(
        self,
        tenant_id: TenantId,
        interview_session_id: UUID,
    ) -> EvaluationReport | None:
        tenant = _require_tenant(tenant_id)
        row = self._db.fetchone(
            "SELECT * FROM evaluation_reports "
            "WHERE tenant_id = ? AND interview_session_id = ?",
            (str(tenant.value), str(interview_session_id)),
        )
        return self._hydrate_report(row)

    def get_by_candidate_and_requisition(
        self,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
    ) -> EvaluationReport | None:
        tenant = _require_tenant(tenant_id)
        row = self._db.fetchone(
            "SELECT * FROM evaluation_reports "
            "WHERE tenant_id = ? AND requisition_id = ? AND candidate_profile_id = ?",
            (str(tenant.value), str(requisition_id), str(candidate_profile_id)),
        )
        return self._hydrate_report(row)


class SqlTenantAccountRepository(BaseSqlRepository):
    """SQL repository implementing TenantAccountRepositoryPort."""

    _TABLE = "tenants"

    def save(self, tenant: TenantAccount) -> None:
        row = tenant_account_to_row(tenant)
        row.pop("tenant_id")
        row.pop("version")
        self._upsert_aggregate(
            aggregate=tenant,
            table=self._TABLE,
            pk_col="tenant_id",
            pk_val=str(tenant.tenant_id.value),
            columns=row,
        )

    def get_by_id(self, tenant_id: TenantId) -> TenantAccount | None:
        tenant = _require_tenant(tenant_id)
        row = self._db.fetchone(
            "SELECT * FROM tenants WHERE tenant_id = ?",
            (str(tenant.value),),
        )
        if row is None:
            return None
        return self._record_hydrated(self._TABLE, row_to_tenant_account(row))

    def get_by_slug(self, organization_slug: str) -> TenantAccount | None:
        slug_norm = organization_slug.strip().lower()
        row = self._db.fetchone(
            "SELECT * FROM tenants WHERE organization_slug = ?",
            (slug_norm,),
        )
        if row is None:
            return None
        return self._record_hydrated(self._TABLE, row_to_tenant_account(row))


class SqlConsentRecordRepository(BaseSqlRepository):
    """SQL repository implementing ConsentRecordRepositoryPort."""

    _TABLE = "consent_records"

    def save(self, record: ConsentRecord) -> None:
        row = consent_record_to_row(record)
        pk_val = str(row.pop("record_id"))
        row.pop("tenant_id")
        row.pop("version")
        self._upsert_aggregate(
            aggregate=record,
            table=self._TABLE,
            pk_col="record_id",
            pk_val=pk_val,
            columns=row,
        )

    def get_by_id(self, tenant_id: TenantId, record_id: UUID) -> ConsentRecord | None:
        tenant = _require_tenant(tenant_id)
        row = self._db.fetchone(
            "SELECT * FROM consent_records WHERE record_id = ? AND tenant_id = ?",
            (str(record_id), str(tenant.value)),
        )
        if row is None:
            return None
        return self._record_hydrated(self._TABLE, row_to_consent_record(row))

    def get_by_candidate_id(
        self,
        tenant_id: TenantId,
        candidate_id: UUID,
    ) -> ConsentRecord | None:
        tenant = _require_tenant(tenant_id)
        row = self._db.fetchone(
            "SELECT * FROM consent_records WHERE tenant_id = ? AND candidate_id = ?",
            (str(tenant.value), str(candidate_id)),
        )
        if row is None:
            return None
        return self._record_hydrated(self._TABLE, row_to_consent_record(row))

    def list_by_tenant(self, tenant_id: TenantId) -> tuple[ConsentRecord, ...]:
        """List all consent records scoped to ``tenant_id``."""
        tenant = _require_tenant(tenant_id)
        rows = self._db.fetchall(
            "SELECT * FROM consent_records WHERE tenant_id = ? ORDER BY record_id",
            (str(tenant.value),),
        )
        return tuple(
            self._record_hydrated(self._TABLE, row_to_consent_record(row)) for row in rows
        )
