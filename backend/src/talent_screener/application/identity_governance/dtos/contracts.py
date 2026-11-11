"""Immutable Request and Response DTOs for Identity & Tenant Governance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from talent_screener.domain.contexts.identity_governance.value_objects import (
    ConsentScope,
    ConsentStatus,
)
from talent_screener.domain.shared.value_objects import TenantId


@dataclass(frozen=True, slots=True)
class ProvisionTenantAccountRequestDTO:
    """Command DTO to provision a new multi-tenant account with quotas & retention."""

    organization_slug: str
    display_name: str
    max_concurrent_batches: int = 5
    max_files_per_batch: int = 500
    max_monthly_interviews: int = 1000
    max_tokens_per_interview: int = 45_000
    subscription_tier: str = "ENTERPRISE"
    pii_retention_days: int = 180
    audit_log_retention_days: int = 2555
    erasure_sla_hours: int = 24
    tenant_id: TenantId | None = None


@dataclass(frozen=True, slots=True)
class ProvisionTenantAccountResponseDTO:
    """Response DTO confirming tenant provisioning."""

    tenant_id: TenantId
    organization_slug: str
    display_name: str
    subscription_tier: str
    pii_retention_days: int
    is_active: bool


@dataclass(frozen=True, slots=True)
class RecordCandidateConsentRequestDTO:
    """Command DTO recording candidate processing consent."""

    tenant_id: TenantId
    candidate_id: UUID
    granted_scopes: tuple[ConsentScope, ...]


@dataclass(frozen=True, slots=True)
class RevokeCandidateConsentRequestDTO:
    """Command DTO withdrawing candidate processing consent."""

    tenant_id: TenantId
    record_id: UUID


@dataclass(frozen=True, slots=True)
class ConsentRecordResponseDTO:
    """Immutable response DTO describing a ConsentRecord state."""

    record_id: UUID
    tenant_id: TenantId
    candidate_id: UUID
    status: ConsentStatus
    granted_scopes: frozenset[ConsentScope]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ExecuteCandidateErasureRequestDTO:
    """Command DTO triggering GDPR Art. 17 crypto-shredding and vector deletion."""

    tenant_id: TenantId
    candidate_id: UUID
    reason: str = "GDPR Article 17 Right to Erasure"


@dataclass(frozen=True, slots=True)
class CandidateErasureReceiptDTO:
    """Immutable audit receipt confirming cryptographic erasure and vector purge."""

    consent_record_id: UUID
    candidate_id: UUID
    status: ConsentStatus
    shredded_at: datetime
    vectors_purged: bool
