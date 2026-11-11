"""Identity & Tenant Governance Bounded Context Aggregate Roots."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from uuid import UUID

from talent_screener.domain.contexts.identity_governance.events import (
    CandidateErasureRequestedEvent,
)
from talent_screener.domain.contexts.identity_governance.value_objects import (
    ConsentScope,
    ConsentStatus,
    QuotaPolicy,
    RetentionPolicy,
)
from talent_screener.domain.shared.base import AggregateRoot
from talent_screener.domain.shared.exceptions import (
    InvalidStateTransitionError,
    InvariantViolationError,
)
from talent_screener.domain.shared.guards import (
    require_int_range,
    require_non_empty_str,
    require_non_nil_uuid,
    require_utc_datetime,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import TenantId, VersionNumber

_ORG_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


class TenantAccount(AggregateRoot[TenantId]):
    """Enterprise tenant account governing quotas, retention policies, and active status."""

    __slots__ = (
        "_active_batches_count",
        "_display_name",
        "_is_active",
        "_monthly_interviews_count",
        "_organization_slug",
        "_quota_policy",
        "_retention_policy",
    )

    def __init__(
        self,
        *,
        tenant_id: TenantId,
        organization_slug: str,
        display_name: str,
        quota_policy: QuotaPolicy | None = None,
        retention_policy: RetentionPolicy | None = None,
        is_active: bool = True,
        version: VersionNumber | None = None,
    ) -> None:
        if not isinstance(tenant_id, TenantId):
            raise InvariantViolationError(
                "tenant_id must be a TenantId.",
                code="INVALID_TENANT_ID",
            )
        super().__init__(tenant_id, tenant_id=tenant_id, version=version)

        cleaned_slug = require_non_empty_str(
            organization_slug, "organization_slug", min_len=3, max_len=64, code="INVALID_ORG_SLUG"
        ).lower()
        if not _ORG_SLUG_RE.fullmatch(cleaned_slug):
            raise InvariantViolationError(
                "organization_slug must be lowercase alphanumeric with internal hyphens.",
                code="INVALID_ORG_SLUG",
            )
        self._organization_slug: str = cleaned_slug
        self._display_name: str = require_non_empty_str(
            display_name, "display_name", min_len=2, max_len=120, code="INVALID_DISPLAY_NAME"
        )
        self._quota_policy: QuotaPolicy = (
            quota_policy if quota_policy is not None else QuotaPolicy()
        )
        self._retention_policy: RetentionPolicy = (
            retention_policy if retention_policy is not None else RetentionPolicy()
        )
        self._is_active: bool = is_active
        self._active_batches_count: int = 0
        self._monthly_interviews_count: int = 0

    @property
    def organization_slug(self) -> str:
        return self._organization_slug

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def quota_policy(self) -> QuotaPolicy:
        return self._quota_policy

    @property
    def retention_policy(self) -> RetentionPolicy:
        return self._retention_policy

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def active_batches_count(self) -> int:
        return self._active_batches_count

    @property
    def monthly_interviews_count(self) -> int:
        return self._monthly_interviews_count

    def ensure_can_accept_batch(self, file_count: int) -> None:
        """Enforce tenant active state, batch file limit, and concurrent batch quota."""
        if not self._is_active:
            raise InvariantViolationError(
                "Suspended tenant cannot submit new screening batches.",
                code="TENANT_SUSPENDED",
            )
        require_int_range(file_count, "file_count", 1, 500, code="BATCH_QUOTA_EXCEEDED")
        if file_count > self._quota_policy.max_files_per_batch:
            raise InvariantViolationError(
                f"Batch file count ({file_count}) exceeds tenant limit "
                f"({self._quota_policy.max_files_per_batch}).",
                code="TENANT_QUOTA_EXCEEDED",
            )
        if self._active_batches_count >= self._quota_policy.max_concurrent_batches:
            raise InvariantViolationError(
                "Tenant has reached maximum concurrent active batches.",
                code="TENANT_QUOTA_EXCEEDED",
            )

    def register_batch_started(self, file_count: int) -> None:
        """Reserve a concurrent batch slot after enforcing quota checks."""
        self.ensure_can_accept_batch(file_count)
        self._active_batches_count += 1
        self._increment_version()

    def release_batch_slot(self) -> None:
        """Release a concurrent batch slot upon batch completion."""
        if self._active_batches_count > 0:
            self._active_batches_count -= 1
            self._increment_version()

    def consume_interview_quota(self) -> None:
        """Consume one monthly interview quota unit or raise TENANT_QUOTA_EXCEEDED."""
        if not self._is_active:
            raise InvariantViolationError(
                "Suspended tenant cannot start interview sessions.",
                code="TENANT_SUSPENDED",
            )
        if self._monthly_interviews_count >= self._quota_policy.max_monthly_interviews:
            raise InvariantViolationError(
                "Tenant monthly interview quota exhausted.",
                code="TENANT_QUOTA_EXCEEDED",
            )
        self._monthly_interviews_count += 1
        self._increment_version()

    def update_quota_policy(self, policy: QuotaPolicy) -> None:
        if not isinstance(policy, QuotaPolicy):
            raise InvariantViolationError(
                "policy must be a QuotaPolicy.", code="INVALID_QUOTA_POLICY"
            )
        self._quota_policy = policy
        self._increment_version()

    def update_retention_policy(self, policy: RetentionPolicy) -> None:
        if not isinstance(policy, RetentionPolicy):
            raise InvariantViolationError(
                "policy must be a RetentionPolicy.", code="INVALID_RETENTION_POLICY"
            )
        self._retention_policy = policy
        self._increment_version()

    def suspend(self) -> None:
        self._is_active = False
        self._increment_version()

    def activate(self) -> None:
        self._is_active = True
        self._increment_version()


class ConsentRecord(AggregateRoot[UUID]):
    """GDPR/CCPA candidate consent and Right-to-Erasure lifecycle aggregate."""

    __slots__ = (
        "_candidate_id",
        "_erasure_requested_at",
        "_expires_at",
        "_granted_at",
        "_granted_scopes",
        "_retention_policy",
        "_shredded_at",
        "_status",
    )

    def __init__(
        self,
        *,
        tenant_id: TenantId,
        candidate_id: UUID,
        granted_scopes: Iterable[ConsentScope],
        retention_policy: RetentionPolicy | None = None,
        granted_at: datetime | None = None,
        record_id: UUID | None = None,
        version: VersionNumber | None = None,
    ) -> None:
        resolved_id = record_id if record_id is not None else generate_uuid7()
        require_non_nil_uuid(resolved_id, "record_id", code="INVALID_CONSENT_RECORD_ID")
        require_non_nil_uuid(candidate_id, "candidate_id", code="INVALID_CANDIDATE_ID")
        super().__init__(resolved_id, tenant_id=tenant_id, version=version)

        scopes = frozenset(granted_scopes)
        if not scopes:
            raise InvariantViolationError(
                "ConsentRecord must specify at least one ConsentScope.",
                code="EMPTY_CONSENT_SCOPES",
            )
        policy = retention_policy if retention_policy is not None else RetentionPolicy()
        ts = (
            require_utc_datetime(granted_at, "granted_at", code="INVALID_UTC_DATETIME")
            if granted_at is not None
            else utc_now()
        )
        self._candidate_id: UUID = candidate_id
        self._granted_scopes: frozenset[ConsentScope] = scopes
        self._retention_policy: RetentionPolicy = policy
        self._granted_at: datetime = ts
        self._expires_at: datetime = ts + timedelta(days=policy.pii_retention_days)
        self._status: ConsentStatus = ConsentStatus.GRANTED
        self._erasure_requested_at: datetime | None = None
        self._shredded_at: datetime | None = None

    @property
    def candidate_id(self) -> UUID:
        return self._candidate_id

    @property
    def granted_scopes(self) -> frozenset[ConsentScope]:
        return self._granted_scopes

    @property
    def retention_policy(self) -> RetentionPolicy:
        return self._retention_policy

    @property
    def granted_at(self) -> datetime:
        return self._granted_at

    @property
    def expires_at(self) -> datetime:
        return self._expires_at

    @property
    def status(self) -> ConsentStatus:
        return self._status

    @property
    def erasure_requested_at(self) -> datetime | None:
        return self._erasure_requested_at

    @property
    def shredded_at(self) -> datetime | None:
        return self._shredded_at

    def is_valid_for_processing(self, *, as_of: datetime | None = None) -> bool:
        """Return True iff consent is GRANTED and retention TTL has not expired."""
        now = (
            require_utc_datetime(as_of, "as_of", code="INVALID_UTC_DATETIME")
            if as_of is not None
            else utc_now()
        )
        return self._status is ConsentStatus.GRANTED and now < self._expires_at

    def withdraw(self) -> None:
        """Withdraw processing consent while awaiting retention cleanup."""
        if self._status is not ConsentStatus.GRANTED:
            raise InvalidStateTransitionError(
                f"Cannot withdraw consent from status {self._status}.",
                from_state=self._status.value,
                to_state=ConsentStatus.WITHDRAWN.value,
                code="INVALID_CONSENT_TRANSITION",
            )
        self._status = ConsentStatus.WITHDRAWN
        self._increment_version()

    def request_erasure(
        self,
        *,
        reason: str = "GDPR Article 17 Right to Erasure",
        requested_at: datetime | None = None,
    ) -> None:
        """Trigger GDPR/CCPA erasure request and emit CandidateErasureRequestedEvent."""
        if self._status in (ConsentStatus.ERASURE_REQUESTED, ConsentStatus.CRYPTO_SHREDDED):
            raise InvalidStateTransitionError(
                f"Cannot request erasure when status is {self._status}.",
                from_state=self._status.value,
                to_state=ConsentStatus.ERASURE_REQUESTED.value,
                code="INVALID_CONSENT_TRANSITION",
            )
        cleaned_reason = require_non_empty_str(
            reason, "reason", max_len=250, code="INVALID_ERASURE_REASON"
        )
        req_ts = (
            require_utc_datetime(requested_at, "requested_at", code="INVALID_UTC_DATETIME")
            if requested_at is not None
            else utc_now()
        )
        self._status = ConsentStatus.ERASURE_REQUESTED
        self._erasure_requested_at = req_ts
        self._increment_version()
        deadline = req_ts + timedelta(hours=self._retention_policy.erasure_sla_hours)
        self._record_event(
            CandidateErasureRequestedEvent(
                aggregate_id=self._id,
                tenant_id=self._tenant_id,
                consent_record_id=self._id,
                candidate_id=self._candidate_id,
                reason=cleaned_reason,
                erasure_deadline_at=deadline,
            )
        )

    def mark_crypto_shredded(self, *, shredded_at: datetime | None = None) -> None:
        """Mark DEK permanently destroyed following GDPR erasure or retention expiration."""
        if self._status is ConsentStatus.CRYPTO_SHREDDED:
            raise InvalidStateTransitionError(
                "ConsentRecord is already crypto-shredded.",
                from_state=self._status.value,
                to_state=ConsentStatus.CRYPTO_SHREDDED.value,
                code="INVALID_CONSENT_TRANSITION",
            )
        ts = (
            require_utc_datetime(shredded_at, "shredded_at", code="INVALID_UTC_DATETIME")
            if shredded_at is not None
            else utc_now()
        )
        self._status = ConsentStatus.CRYPTO_SHREDDED
        self._shredded_at = ts
        self._increment_version()
