"""Inbound Driving Port protocols for Recruitment Management Use Cases."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.recruitment.dtos import (
    ArchiveJobRequisitionRequestDTO,
    ArchiveJobRequisitionResponseDTO,
    CreateJobRequisitionRequestDTO,
    CreateJobRequisitionResponseDTO,
    GetJobRequisitionQueryDTO,
    JobRequisitionReadDto,
    LockJobRequisitionRubricRequestDTO,
    LockJobRequisitionRubricResponseDTO,
    PublishJobRequisitionRequestDTO,
    PublishJobRequisitionResponseDTO,
    UpdateJobRubricRequestDTO,
    UpdateJobRubricResponseDTO,
)


@runtime_checkable
class CreateJobRequisitionPort(Protocol):
    """Inbound port for creating draft JobRequisitions."""

    async def execute(
        self, request: CreateJobRequisitionRequestDTO
    ) -> CreateJobRequisitionResponseDTO: ...


@runtime_checkable
class PublishJobRequisitionPort(Protocol):
    """Inbound port for publishing JobRequisitions."""

    async def execute(
        self, request: PublishJobRequisitionRequestDTO
    ) -> PublishJobRequisitionResponseDTO: ...


@runtime_checkable
class UpdateJobRubricPort(Protocol):
    """Inbound port for updating unlocked JobRequisition rubrics."""

    async def execute(self, request: UpdateJobRubricRequestDTO) -> UpdateJobRubricResponseDTO: ...


@runtime_checkable
class LockJobRequisitionRubricPort(Protocol):
    """Inbound port for locking a JobRequisition rubric on active workflow start."""

    async def execute(
        self, request: LockJobRequisitionRubricRequestDTO
    ) -> LockJobRequisitionRubricResponseDTO: ...


@runtime_checkable
class ArchiveJobRequisitionPort(Protocol):
    """Inbound port for archiving JobRequisitions."""

    async def execute(
        self, request: ArchiveJobRequisitionRequestDTO
    ) -> ArchiveJobRequisitionResponseDTO: ...


@runtime_checkable
class GetJobRequisitionPort(Protocol):
    """Inbound query port for reading JobRequisition projections."""

    async def execute(self, query: GetJobRequisitionQueryDTO) -> JobRequisitionReadDto: ...
