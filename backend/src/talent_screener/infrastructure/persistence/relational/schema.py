"""Relational Persistence Schema (PostgreSQL & SQLite DDL) and ACID DB-API 2.0 Manager."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class SqlDialect(StrEnum):
    """Supported relational SQL dialects for dual SQLite/PostgreSQL DDL rendering."""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


@dataclass(frozen=True, slots=True)
class DatabaseConnectionConfig:
    """Configuration governing local SQLite ACID execution or PostgreSQL target DDL."""

    dsn: str = ":memory:"
    dialect: SqlDialect = SqlDialect.SQLITE
    enable_wal: bool = True
    busy_timeout_ms: int = 5000


def _type_map(dialect: SqlDialect) -> Mapping[str, str]:
    if dialect is SqlDialect.POSTGRESQL:
        return {
            "UUID": "UUID",
            "TEXT": "TEXT",
            "INT": "INTEGER",
            "FLOAT": "DOUBLE PRECISION",
            "BOOL": "BOOLEAN",
            "TS": "TIMESTAMPTZ",
            "JSON": "JSONB",
            "BLOB": "BYTEA",
        }
    return {
        "UUID": "TEXT",
        "TEXT": "TEXT",
        "INT": "INTEGER",
        "FLOAT": "REAL",
        "BOOL": "INTEGER",
        "TS": "TEXT",
        "JSON": "TEXT",
        "BLOB": "BLOB",
    }


def get_ddl_statements(dialect: SqlDialect = SqlDialect.SQLITE) -> tuple[str, ...]:
    """Generate ordered, idempotent DDL statements for all Phase 6 relational tables."""
    t = _type_map(dialect)
    return (
        f"""
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id {t['UUID']} PRIMARY KEY,
            organization_slug {t['TEXT']} NOT NULL UNIQUE,
            display_name {t['TEXT']} NOT NULL,
            quota_policy_json {t['JSON']} NOT NULL,
            retention_policy_json {t['JSON']} NOT NULL,
            is_active {t['BOOL']} NOT NULL,
            active_batches_count {t['INT']} NOT NULL DEFAULT 0,
            monthly_interviews_count {t['INT']} NOT NULL DEFAULT 0,
            version {t['INT']} NOT NULL DEFAULT 1,
            updated_at {t['TS']} NOT NULL
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS consent_records (
            record_id {t['UUID']} PRIMARY KEY,
            tenant_id {t['UUID']} NOT NULL,
            candidate_id {t['UUID']} NOT NULL,
            granted_scopes_json {t['JSON']} NOT NULL,
            retention_policy_json {t['JSON']} NOT NULL,
            granted_at {t['TS']} NOT NULL,
            expires_at {t['TS']} NOT NULL,
            status {t['TEXT']} NOT NULL,
            erasure_requested_at {t['TS']},
            shredded_at {t['TS']},
            version {t['INT']} NOT NULL DEFAULT 1,
            UNIQUE (tenant_id, candidate_id)
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_consent_tenant_status "
        "ON consent_records (tenant_id, status)",
        f"""
        CREATE TABLE IF NOT EXISTS job_requisitions (
            requisition_id {t['UUID']} PRIMARY KEY,
            tenant_id {t['UUID']} NOT NULL,
            title {t['TEXT']} NOT NULL,
            description {t['TEXT']} NOT NULL,
            seniority_level {t['TEXT']} NOT NULL,
            status {t['TEXT']} NOT NULL,
            gatekeepers_json {t['JSON']} NOT NULL,
            tier_thresholds_json {t['JSON']} NOT NULL,
            weight_vector_json {t['JSON']} NOT NULL,
            interview_timebox_minutes {t['INT']} NOT NULL,
            rubric_version {t['INT']} NOT NULL,
            rubric_version_id {t['UUID']} NOT NULL,
            active_screening_batch_ids_json {t['JSON']} NOT NULL,
            active_interview_session_ids_json {t['JSON']} NOT NULL,
            has_completed_interviews {t['BOOL']} NOT NULL DEFAULT 0,
            version {t['INT']} NOT NULL DEFAULT 1
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_requisitions_tenant_status "
        "ON job_requisitions (tenant_id, status)",
        f"""
        CREATE TABLE IF NOT EXISTS competency_criteria (
            criterion_id {t['UUID']} PRIMARY KEY,
            requisition_id {t['UUID']} NOT NULL
                REFERENCES job_requisitions(requisition_id) ON DELETE CASCADE,
            tenant_id {t['UUID']} NOT NULL,
            sort_order {t['INT']} NOT NULL,
            name {t['TEXT']} NOT NULL,
            canonical_skill_id {t['TEXT']} NOT NULL,
            weight {t['FLOAT']} NOT NULL,
            target_depth {t['INT']} NOT NULL,
            is_gatekeeper {t['BOOL']} NOT NULL,
            raw_importance {t['FLOAT']} NOT NULL,
            ideal_anchor_descriptor {t['TEXT']} NOT NULL
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_criteria_req_tenant "
        "ON competency_criteria (tenant_id, requisition_id, sort_order)",
        f"""
        CREATE TABLE IF NOT EXISTS screening_batches (
            batch_id {t['UUID']} PRIMARY KEY,
            tenant_id {t['UUID']} NOT NULL,
            requisition_id {t['UUID']} NOT NULL,
            batch_label {t['TEXT']} NOT NULL,
            total_files {t['INT']} NOT NULL,
            status {t['TEXT']} NOT NULL,
            completed_count {t['INT']} NOT NULL,
            failed_count {t['INT']} NOT NULL,
            pending_count {t['INT']} NOT NULL,
            seen_hashes_json {t['JSON']} NOT NULL,
            version {t['INT']} NOT NULL DEFAULT 1
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_batches_tenant_req "
        "ON screening_batches (tenant_id, requisition_id)",
        f"""
        CREATE TABLE IF NOT EXISTS document_ingestion_tasks (
            task_id {t['UUID']} PRIMARY KEY,
            batch_id {t['UUID']} NOT NULL
                REFERENCES screening_batches(batch_id) ON DELETE CASCADE,
            tenant_id {t['UUID']} NOT NULL,
            sort_order {t['INT']} NOT NULL,
            filename {t['TEXT']} NOT NULL,
            sha256_hex {t['TEXT']} NOT NULL,
            file_size_bytes {t['INT']} NOT NULL,
            mime_type {t['TEXT']} NOT NULL,
            status {t['TEXT']} NOT NULL,
            candidate_profile_id {t['UUID']},
            error_code {t['TEXT']},
            error_message {t['TEXT']},
            retry_count {t['INT']} NOT NULL DEFAULT 0
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_tasks_batch_tenant "
        "ON document_ingestion_tasks (tenant_id, batch_id, sort_order)",
        f"""
        CREATE TABLE IF NOT EXISTS candidate_profiles (
            profile_id {t['UUID']} PRIMARY KEY,
            tenant_id {t['UUID']} NOT NULL,
            anonymized_token {t['UUID']} NOT NULL,
            file_sha256_hex {t['TEXT']} NOT NULL,
            file_size_bytes {t['INT']} NOT NULL,
            mime_type {t['TEXT']} NOT NULL,
            pii_vault_ref_id {t['UUID']} NOT NULL,
            token_categories_json {t['JSON']} NOT NULL,
            work_experiences_json {t['JSON']} NOT NULL,
            projects_json {t['JSON']} NOT NULL,
            skill_claims_json {t['JSON']} NOT NULL,
            seniority_level {t['TEXT']},
            verified_experience_months {t['INT']},
            seniority_confidence {t['FLOAT']},
            version {t['INT']} NOT NULL DEFAULT 1,
            UNIQUE (tenant_id, anonymized_token)
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_profiles_tenant "
        "ON candidate_profiles (tenant_id, profile_id)",
        f"""
        CREATE TABLE IF NOT EXISTS candidate_pii_vault (
            vault_reference_id {t['UUID']} PRIMARY KEY,
            tenant_id {t['UUID']} NOT NULL,
            candidate_id {t['UUID']} NOT NULL,
            key_id {t['UUID']} NOT NULL,
            wrapped_dek {t['BLOB']} NOT NULL,
            encrypted_payload {t['BLOB']} NOT NULL,
            token_categories_json {t['JSON']} NOT NULL,
            blind_email_hmac_index {t['TEXT']},
            is_shredded {t['BOOL']} NOT NULL DEFAULT 0,
            shredded_at {t['TS']},
            erasure_reason {t['TEXT']},
            UNIQUE (tenant_id, candidate_id)
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_pii_vault_tenant_hmac "
        "ON candidate_pii_vault (tenant_id, blind_email_hmac_index)",
        f"""
        CREATE TABLE IF NOT EXISTS semantic_match_results (
            match_result_id {t['UUID']} PRIMARY KEY,
            tenant_id {t['UUID']} NOT NULL,
            requisition_id {t['UUID']} NOT NULL,
            candidate_profile_id {t['UUID']} NOT NULL,
            rubric_version {t['INT']} NOT NULL,
            match_vector_json {t['JSON']} NOT NULL,
            alignment_items_json {t['JSON']} NOT NULL,
            skill_gaps_json {t['JSON']} NOT NULL,
            tier {t['TEXT']} NOT NULL,
            failed_gatekeepers_json {t['JSON']} NOT NULL,
            version {t['INT']} NOT NULL DEFAULT 1,
            UNIQUE (tenant_id, requisition_id, candidate_profile_id)
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_matches_tenant_req "
        "ON semantic_match_results (tenant_id, requisition_id)",
        f"""
        CREATE TABLE IF NOT EXISTS interview_sessions (
            session_id {t['UUID']} PRIMARY KEY,
            tenant_id {t['UUID']} NOT NULL,
            requisition_id {t['UUID']} NOT NULL,
            candidate_profile_id {t['UUID']} NOT NULL,
            rubric_version {t['INT']} NOT NULL,
            competency_queue_json {t['JSON']} NOT NULL,
            active_competency_id {t['UUID']},
            current_difficulty {t['INT']} NOT NULL,
            max_turns {t['INT']} NOT NULL,
            token_budget_limit {t['INT']} NOT NULL,
            tokens_consumed {t['INT']} NOT NULL,
            status {t['TEXT']} NOT NULL,
            telemetry_snapshots_json {t['JSON']} NOT NULL,
            integrity_flags_json {t['JSON']} NOT NULL,
            probe_counts_json {t['JSON']} NOT NULL,
            termination_reason {t['TEXT']},
            version {t['INT']} NOT NULL DEFAULT 1
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_sessions_tenant_status "
        "ON interview_sessions (tenant_id, status)",
        f"""
        CREATE TABLE IF NOT EXISTS interview_turns (
            turn_id {t['UUID']} PRIMARY KEY,
            session_id {t['UUID']} NOT NULL
                REFERENCES interview_sessions(session_id) ON DELETE CASCADE,
            tenant_id {t['UUID']} NOT NULL,
            turn_number {t['INT']} NOT NULL,
            competency_id {t['UUID']} NOT NULL,
            difficulty {t['INT']} NOT NULL,
            question_prompt {t['TEXT']} NOT NULL,
            prompt_hash_sha256 {t['TEXT']} NOT NULL,
            prompt_template_version {t['TEXT']} NOT NULL,
            candidate_response {t['TEXT']} NOT NULL,
            evaluation_json {t['JSON']} NOT NULL,
            tokens_used {t['INT']} NOT NULL,
            socratic_probe_json {t['JSON']},
            latency_metric_json {t['JSON']},
            integrity_flags_json {t['JSON']} NOT NULL,
            UNIQUE (session_id, turn_number)
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_turns_tenant_session "
        "ON interview_turns (tenant_id, session_id, turn_number)",
        f"""
        CREATE TABLE IF NOT EXISTS evaluation_reports (
            report_id {t['UUID']} PRIMARY KEY,
            tenant_id {t['UUID']} NOT NULL,
            interview_session_id {t['UUID']} NOT NULL,
            requisition_id {t['UUID']} NOT NULL,
            candidate_profile_id {t['UUID']} NOT NULL,
            scorecard_items_json {t['JSON']} NOT NULL,
            composite_score_json {t['JSON']} NOT NULL,
            authenticity_index_json {t['JSON']} NOT NULL,
            initial_band {t['TEXT']} NOT NULL,
            effective_band {t['TEXT']} NOT NULL,
            executive_summary {t['TEXT']} NOT NULL,
            integrity_entries_json {t['JSON']} NOT NULL,
            valid_turn_numbers_json {t['JSON']},
            is_finalized {t['BOOL']} NOT NULL DEFAULT 0,
            report_hash_hex {t['TEXT']},
            version {t['INT']} NOT NULL DEFAULT 1,
            UNIQUE (tenant_id, interview_session_id)
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_reports_tenant_req_cand "
        "ON evaluation_reports (tenant_id, requisition_id, candidate_profile_id)",
        f"""
        CREATE TABLE IF NOT EXISTS recruiter_override_audits (
            override_id {t['UUID']} PRIMARY KEY,
            report_id {t['UUID']} NOT NULL
                REFERENCES evaluation_reports(report_id) ON DELETE CASCADE,
            tenant_id {t['UUID']} NOT NULL,
            sort_order {t['INT']} NOT NULL,
            actor_id {t['UUID']} NOT NULL,
            previous_band {t['TEXT']} NOT NULL,
            new_band {t['TEXT']} NOT NULL,
            justification {t['TEXT']} NOT NULL,
            recorded_at {t['TS']} NOT NULL
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS transactional_outbox (
            outbox_id {t['UUID']} PRIMARY KEY,
            tenant_id {t['UUID']} NOT NULL,
            aggregate_type {t['TEXT']} NOT NULL,
            aggregate_id {t['UUID']} NOT NULL,
            event_id {t['UUID']} NOT NULL UNIQUE,
            event_type {t['TEXT']} NOT NULL,
            schema_version {t['TEXT']} NOT NULL,
            payload_json {t['JSON']} NOT NULL,
            occurred_at {t['TS']} NOT NULL,
            status {t['TEXT']} NOT NULL,
            retry_count {t['INT']} NOT NULL DEFAULT 0,
            last_error {t['TEXT']},
            next_retry_at {t['TS']},
            published_at {t['TS']}
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_outbox_status_occurred "
        "ON transactional_outbox (status, occurred_at)",
        "CREATE INDEX IF NOT EXISTS ix_outbox_tenant_status "
        "ON transactional_outbox (tenant_id, status, occurred_at)",
        f"""
        CREATE TABLE IF NOT EXISTS idempotency_records (
            tenant_id {t['UUID']} NOT NULL,
            operation {t['TEXT']} NOT NULL,
            idempotency_key {t['TEXT']} NOT NULL,
            payload_hash {t['TEXT']} NOT NULL,
            status {t['TEXT']} NOT NULL,
            resource_id {t['UUID']},
            serialized_result_json {t['JSON']},
            created_at {t['TS']} NOT NULL,
            updated_at {t['TS']} NOT NULL,
            PRIMARY KEY (tenant_id, operation, idempotency_key)
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS interview_state_checkpoints (
            tenant_id {t['UUID']} NOT NULL,
            session_id {t['UUID']} NOT NULL,
            checkpoint_version {t['TEXT']} NOT NULL,
            step_number {t['INT']} NOT NULL,
            fencing_token {t['INT']} NOT NULL,
            parent_checkpoint_version {t['TEXT']},
            state_json {t['JSON']} NOT NULL,
            state_sha256 {t['TEXT']} NOT NULL,
            saved_at {t['TS']} NOT NULL,
            PRIMARY KEY (tenant_id, session_id, checkpoint_version)
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS ix_checkpoints_latest "
        "ON interview_state_checkpoints (tenant_id, session_id, step_number DESC)",
        f"""
        CREATE TABLE IF NOT EXISTS vector_sync_watermarks (
            tenant_id {t['UUID']} NOT NULL,
            candidate_profile_id {t['UUID']} NOT NULL,
            latest_sync_version {t['INT']} NOT NULL,
            chunk_count {t['INT']} NOT NULL DEFAULT 0,
            is_erased {t['BOOL']} NOT NULL DEFAULT 0,
            updated_at {t['TS']} NOT NULL,
            PRIMARY KEY (tenant_id, candidate_profile_id)
        )
        """.strip(),
    )


class SqlDatabaseManager:
    """Thread-safe ACID SQLite connection manager with WAL support and PG placeholder mapping."""

    def __init__(
        self,
        config: DatabaseConnectionConfig | None = None,
        *,
        auto_initialize: bool = True,
    ) -> None:
        self._config = config if config is not None else DatabaseConnectionConfig()
        if self._config.dialect is not SqlDialect.SQLITE:
            raise ValueError(
                "Local runtime SqlDatabaseManager executes against SQLite DB-API 2.0; "
                "use get_ddl_statements(SqlDialect.POSTGRESQL) for PG schema export."
            )
        if self._config.dsn != ":memory:":
            Path(self._config.dsn).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self._config.dsn,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._in_transaction = False
        self._rollback_hooks: list[Callable[[], None]] = []
        self._configure_pragmas()
        if auto_initialize:
            self.initialize_schema()

    def _configure_pragmas(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute(f"PRAGMA busy_timeout = {max(100, self._config.busy_timeout_ms)}")
            if self._config.dsn != ":memory:" and self._config.enable_wal:
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = NORMAL")

    @property
    def dialect(self) -> SqlDialect:
        return self._config.dialect

    @property
    def dsn(self) -> str:
        return self._config.dsn

    @property
    def in_transaction(self) -> bool:
        return self._in_transaction

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @staticmethod
    def _adapt_sql(sql: str) -> str:
        if "%s" in sql:
            return sql.replace("%s", "?")
        return sql

    def initialize_schema(self) -> None:
        """Create all relational tables and indexes idempotently."""
        with self._lock:
            for stmt in get_ddl_statements(SqlDialect.SQLITE):
                self._conn.execute(stmt)

    def table_names(self) -> tuple[str, ...]:
        """List all non-internal relational tables currently present."""
        rows = self.fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return tuple(str(row["name"]) for row in rows)

    def register_rollback_hook(self, hook: Callable[[], None]) -> None:
        """Register an in-memory state compensation callback executed on transaction rollback."""
        if self._in_transaction:
            self._rollback_hooks.append(hook)

    def begin_transaction(self) -> None:
        """Begin an explicit IMMEDIATE ACID transaction."""
        with self._lock:
            if not self._in_transaction:
                self._conn.execute("BEGIN IMMEDIATE")
                self._in_transaction = True
                self._rollback_hooks.clear()

    def commit_transaction(self) -> None:
        """Commit the active ACID transaction and discard rollback compensation hooks."""
        with self._lock:
            if self._in_transaction:
                self._conn.execute("COMMIT")
                self._in_transaction = False
                self._rollback_hooks.clear()

    def rollback_transaction(self) -> None:
        """Roll back the active transaction and invoke registered compensation hooks."""
        with self._lock:
            hooks = list(self._rollback_hooks)
            self._rollback_hooks.clear()
            if self._in_transaction:
                try:
                    self._conn.execute("ROLLBACK")
                finally:
                    self._in_transaction = False
            for hook in reversed(hooks):
                hook()

    @contextmanager
    def write_transaction(self) -> Iterator[None]:
        """Use active UoW transaction if present, otherwise open/commit a local transaction."""
        if self._in_transaction:
            yield
            return
        with self._lock:
            self.begin_transaction()
            try:
                yield
            except Exception:
                self.rollback_transaction()
                raise
            else:
                self.commit_transaction()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(self._adapt_sql(sql), tuple(params))

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(self._adapt_sql(sql), [tuple(p) for p in seq_of_params])

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(self._adapt_sql(sql), tuple(params)).fetchone()
            return row if isinstance(row, sqlite3.Row) else None

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> tuple[sqlite3.Row, ...]:
        with self._lock:
            rows = self._conn.execute(self._adapt_sql(sql), tuple(params)).fetchall()
            return tuple(rows)

    def close(self) -> None:
        with self._lock:
            if self._in_transaction:
                self.rollback_transaction()
            self._conn.close()
