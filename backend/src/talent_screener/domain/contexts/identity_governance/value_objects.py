"""Identity & Tenant Governance Bounded Context Value Objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from talent_screener.domain.shared.exceptions import InvariantViolationError
from talent_screener.domain.shared.guards import require_int_range, require_non_empty_str
from talent_screener.domain.shared.value_objects import ValueObject


class ConsentScope(StrEnum):
    """Granular candidate consent scopes."""

    AI_SCREENING = "AI_SCREENING"
    VOICE_INTERVIEW_RECORDING = "VOICE_INTERVIEW_RECORDING"
    INTEGRITY_TELEMETRY = "INTEGRITY_TELEMETRY"


class ConsentStatus(StrEnum):
    """Lifecycle states of candidate GDPR/CCPA consent and erasure."""

    GRANTED = "GRANTED"
    WITHDRAWN = "WITHDRAWN"
    ERASURE_REQUESTED = "ERASURE_REQUESTED"
    CRYPTO_SHREDDED = "CRYPTO_SHREDDED"


@dataclass(frozen=True, slots=True)
class QuotaPolicy(ValueObject):
    """Multi-tenant compute, batch, and interview quota ceilings."""

    max_concurrent_batches: int = 5
    max_files_per_batch: int = 500
    max_monthly_interviews: int = 1000
    max_tokens_per_interview: int = 45_000
    subscription_tier: str = "ENTERPRISE"

    _VALID_TIERS = frozenset({"STARTER", "GROWTH", "ENTERPRISE"})

    def __post_init__(self) -> None:
        require_int_range(
            self.max_concurrent_batches,
            "max_concurrent_batches",
            1,
            100,
            code="INVALID_QUOTA_POLICY",
        )
        require_int_range(
            self.max_files_per_batch, "max_files_per_batch", 1, 500, code="INVALID_QUOTA_POLICY"
        )
        require_int_range(
            self.max_monthly_interviews, "max_monthly_interviews", 1, code="INVALID_QUOTA_POLICY"
        )
        require_int_range(
            self.max_tokens_per_interview,
            "max_tokens_per_interview",
            1000,
            1_000_000,
            code="INVALID_QUOTA_POLICY",
        )
        tier = require_non_empty_str(
            self.subscription_tier, "subscription_tier", max_len=32, code="INVALID_QUOTA_POLICY"
        ).upper()
        if tier not in self._VALID_TIERS:
            raise InvariantViolationError(
                f"Invalid subscription_tier: {tier!r}.",
                code="INVALID_QUOTA_POLICY",
            )
        object.__setattr__(self, "subscription_tier", tier)


@dataclass(frozen=True, slots=True)
class RetentionPolicy(ValueObject):
    """Data governance retention windows and right-to-erasure SLA bounds."""

    pii_retention_days: int = 180
    audit_log_retention_days: int = 2555
    erasure_sla_hours: int = 24
    auto_shred_on_expiry: bool = True

    def __post_init__(self) -> None:
        pii_days = require_int_range(
            self.pii_retention_days, "pii_retention_days", 1, 3650, code="INVALID_RETENTION_POLICY"
        )
        require_int_range(
            self.audit_log_retention_days,
            "audit_log_retention_days",
            pii_days,
            3650,
            code="INVALID_RETENTION_POLICY",
        )
        require_int_range(
            self.erasure_sla_hours, "erasure_sla_hours", 1, 72, code="INVALID_RETENTION_POLICY"
        )
        if not isinstance(self.auto_shred_on_expiry, bool):
            raise InvariantViolationError(
                "auto_shred_on_expiry must be a boolean.",
                code="INVALID_RETENTION_POLICY",
            )
