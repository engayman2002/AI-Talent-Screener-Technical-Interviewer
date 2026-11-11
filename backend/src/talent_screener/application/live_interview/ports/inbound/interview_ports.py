"""Inbound Driving Ports for Live Adaptive Interview Use Cases."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.live_interview.dtos import (
    CompleteInterviewSessionRequestDTO,
    CompleteInterviewSessionResponseDTO,
    InitializeInterviewSessionRequestDTO,
    InitializeInterviewSessionResponseDTO,
    ProcessCandidateTurnRequestDTO,
    ProcessCandidateTurnResponseDTO,
    RecordClientTelemetryRequestDTO,
    RecordClientTelemetryResponseDTO,
    TerminateInterviewEarlyRequestDTO,
    TerminateInterviewEarlyResponseDTO,
)


@runtime_checkable
class InitializeInterviewSessionPort(Protocol):
    """Inbound port for provisioning and starting an InterviewSession."""

    async def execute(
        self, request: InitializeInterviewSessionRequestDTO
    ) -> InitializeInterviewSessionResponseDTO: ...


@runtime_checkable
class ProcessCandidateTurnPort(Protocol):
    """Inbound port for executing a single guarded adaptive interview turn."""

    async def execute(
        self, request: ProcessCandidateTurnRequestDTO
    ) -> ProcessCandidateTurnResponseDTO: ...


@runtime_checkable
class RecordClientTelemetryPort(Protocol):
    """Inbound port for ingesting real-time client integrity telemetry."""

    async def execute(
        self, request: RecordClientTelemetryRequestDTO
    ) -> RecordClientTelemetryResponseDTO: ...


@runtime_checkable
class TerminateInterviewEarlyPort(Protocol):
    """Inbound port for terminating an InterviewSession early."""

    async def execute(
        self, request: TerminateInterviewEarlyRequestDTO
    ) -> TerminateInterviewEarlyResponseDTO: ...


@runtime_checkable
class CompleteInterviewSessionPort(Protocol):
    """Inbound port for completing an InterviewSession."""

    async def execute(
        self, request: CompleteInterviewSessionRequestDTO
    ) -> CompleteInterviewSessionResponseDTO: ...
