"""Live Interview Bounded Context Repository Port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.domain.contexts.live_interview.aggregates import InterviewSession
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class InterviewSessionRepositoryPort(Protocol):
    """Persistence contract for InterviewSession aggregate roots."""

    def save(self, session: InterviewSession) -> None: ...

    def get_by_id(self, tenant_id: TenantId, session_id: UUID) -> InterviewSession | None: ...
