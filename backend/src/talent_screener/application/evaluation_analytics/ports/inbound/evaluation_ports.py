"""Inbound Driving Ports for Evaluation & Analytics Use Cases."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.evaluation_analytics.dtos import (
    BuildCandidateComparisonMatrixRequestDTO,
    CandidateComparisonMatrixDto,
    EvaluationReportResponseDTO,
    GenerateEvaluationReportRequestDTO,
    RecordRecruiterOverrideRequestDTO,
)


@runtime_checkable
class GenerateEvaluationReportPort(Protocol):
    """Inbound port for synthesizing and finalizing an EvaluationReport."""

    async def execute(
        self, request: GenerateEvaluationReportRequestDTO
    ) -> EvaluationReportResponseDTO: ...


@runtime_checkable
class RecordRecruiterOverridePort(Protocol):
    """Inbound port for recording a human RecruiterOverrideAudit."""

    async def execute(
        self, request: RecordRecruiterOverrideRequestDTO
    ) -> EvaluationReportResponseDTO: ...


@runtime_checkable
class BuildCandidateComparisonMatrixPort(Protocol):
    """Inbound port for constructing a 2-4 candidate Side-by-Side Comparison Matrix."""

    async def execute(
        self, request: BuildCandidateComparisonMatrixRequestDTO
    ) -> CandidateComparisonMatrixDto: ...
