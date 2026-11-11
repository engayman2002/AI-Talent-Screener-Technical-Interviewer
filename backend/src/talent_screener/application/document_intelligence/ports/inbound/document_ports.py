"""Inbound Driving Ports for Document Intelligence & Semantic Matching Use Cases."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.document_intelligence.dtos import (
    ExecuteHybridMatchingRequestDTO,
    GetScreeningBatchStatusQueryDTO,
    HybridMatchResponseDTO,
    InitiateScreeningBatchRequestDTO,
    InitiateScreeningBatchResponseDTO,
    ProcessDocumentIngestionTaskRequestDTO,
    ProcessDocumentIngestionTaskResponseDTO,
    ScreeningBatchStatusDTO,
)


@runtime_checkable
class InitiateScreeningBatchPort(Protocol):
    """Inbound port for initiating an idempotent bulk CV ScreeningBatch."""

    async def execute(
        self, request: InitiateScreeningBatchRequestDTO
    ) -> InitiateScreeningBatchResponseDTO: ...


@runtime_checkable
class ProcessDocumentIngestionTaskPort(Protocol):
    """Inbound port for executing the 6-stage ingestion pipeline on a single CV."""

    async def execute(
        self, request: ProcessDocumentIngestionTaskRequestDTO
    ) -> ProcessDocumentIngestionTaskResponseDTO: ...


@runtime_checkable
class ExecuteHybridMatchingPort(Protocol):
    """Inbound port for evaluating hybrid semantic match and tiering."""

    async def execute(
        self, request: ExecuteHybridMatchingRequestDTO
    ) -> HybridMatchResponseDTO: ...


@runtime_checkable
class GetScreeningBatchStatusPort(Protocol):
    """Inbound query port for retrieving ScreeningBatch progress."""

    async def execute(
        self, query: GetScreeningBatchStatusQueryDTO
    ) -> ScreeningBatchStatusDTO: ...
