"""Semantic Matching Engine Repository Port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.domain.contexts.document_intelligence.matching.aggregates import (
    SemanticMatchResult,
)
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class SemanticMatchResultRepositoryPort(Protocol):
    """Persistence contract for SemanticMatchResult aggregate roots."""

    def save(self, match_result: SemanticMatchResult) -> None: ...

    def get_by_id(
        self,
        tenant_id: TenantId,
        match_result_id: UUID,
    ) -> SemanticMatchResult | None: ...

    def list_by_requisition(
        self,
        tenant_id: TenantId,
        requisition_id: UUID,
    ) -> tuple[SemanticMatchResult, ...]: ...
