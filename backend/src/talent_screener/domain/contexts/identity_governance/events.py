"""Identity & Tenant Governance Bounded Context Domain Events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from talent_screener.domain.shared.base import DomainEvent


@dataclass(frozen=True, slots=True)
class CandidateErasureRequestedEvent(DomainEvent):
    """Emitted when a candidate triggers GDPR/CCPA Right to Erasure."""

    consent_record_id: UUID
    candidate_id: UUID
    reason: str
    erasure_deadline_at: datetime
