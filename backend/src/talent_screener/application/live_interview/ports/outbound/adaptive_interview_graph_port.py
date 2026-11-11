"""Outbound Application Port isolating stateful interview graph execution."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.live_interview.dtos import (
    CognitiveGraphContextDTO,
    CognitiveGraphResultDTO,
    InterviewInitCommandDTO,
    InterviewInitOutcomeDTO,
)
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class AdaptiveInterviewGraphPort(Protocol):
    """Port abstracting stateful adaptive interview orchestration behind an ACL."""

    async def initialize_session_graph(
        self,
        command: InterviewInitCommandDTO,
    ) -> InterviewInitOutcomeDTO: ...

    async def execute_cognitive_turn(
        self,
        context: CognitiveGraphContextDTO,
    ) -> CognitiveGraphResultDTO: ...

    async def finalize_session_graph(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        checkpoint_version: str,
    ) -> str: ...
