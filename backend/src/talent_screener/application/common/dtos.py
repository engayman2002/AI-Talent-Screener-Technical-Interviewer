"""Immutable common Application Layer DTOs crossing cross-cutting outbound ports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.value_objects import PiiRedactionMap
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import SHA256Hash, TenantId


class IdempotencyStatus(StrEnum):
    """Lifecycle status of an idempotent operation reservation."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class FailureClassification(StrEnum):
    """Deterministic fault taxonomy separating retryable transient faults from permanent errors."""

    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"


@dataclass(frozen=True, slots=True)
class IdempotencyRecordDTO:
    """Stored idempotency reservation or completed execution receipt."""

    tenant_id: TenantId
    operation: str
    idempotency_key: str
    payload_hash: str
    status: IdempotencyStatus
    resource_id: UUID | None = None
    serialized_result: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class FencingLeaseDTO:
    """Distributed mutual-exclusion lease carrying a monotonic split-brain fencing token."""

    tenant_id: TenantId
    resource_key: str
    fencing_token: int
    lease_id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VirusScanOutcomeDTO:
    """Security verification result emitted by VirusScannerPort."""

    is_clean: bool
    detected_signature: str | None
    verified_mime_type: str
    verified_sha256: SHA256Hash
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class PiiTokenizationOutcomeDTO:
    """Zero-trust PII redaction and KMS vault reference returned by PiiVaultPort."""

    anonymized_text: str
    pii_redaction_map: PiiRedactionMap
    vault_reference_id: UUID


@dataclass(frozen=True, slots=True)
class CryptoShredReceiptDTO:
    """Immutable GDPR Article 17 cryptographic erasure receipt."""

    tenant_id: TenantId
    candidate_id: UUID
    shredded_at: datetime
    already_shredded: bool = False


@dataclass(frozen=True, slots=True)
class TaskDispatchDTO:
    """Asynchronous background worker dispatch payload with correlation metadata."""

    tenant_id: TenantId
    queue_name: str
    task_name: str
    correlation_id: str
    payload: Mapping[str, object]
    priority: int = 5


@dataclass(frozen=True, slots=True)
class RealtimeProgressEventDTO:
    """Realtime SSE / WebSocket telemetry notification payload."""

    tenant_id: TenantId
    channel: str
    event_type: str
    payload: Mapping[str, object]
    emitted_at: datetime = field(default_factory=utc_now, kw_only=True)


@dataclass(frozen=True, slots=True)
class TokenUsageRecordDTO:
    """FinOps LLM token and micro-USD cost accounting record."""

    tenant_id: TenantId
    session_id: UUID
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_micro_usd: int


@dataclass(frozen=True, slots=True)
class DeadLetterRecordDTO:
    """Immutable failed-task envelope routed to DeadLetterQueuePort."""

    tenant_id: TenantId
    task_name: str
    correlation_id: str
    payload: Mapping[str, object]
    error_code: str
    error_message: str
    attempt_count: int
    classification: FailureClassification
    record_id: UUID = field(default_factory=generate_uuid7, kw_only=True)
    failed_at: datetime = field(default_factory=utc_now, kw_only=True)
