"""Use Case interactors for Recruitment Management Bounded Context."""

from __future__ import annotations

from collections.abc import Sequence

from talent_screener.application.common.exceptions import ResourceNotFoundError
from talent_screener.application.common.ports.outbound import TransactionalUnitOfWorkPort
from talent_screener.application.recruitment.dtos import (
    ArchiveJobRequisitionRequestDTO,
    ArchiveJobRequisitionResponseDTO,
    CompetencyCriterionInputDTO,
    CompetencyCriterionReadDTO,
    CreateJobRequisitionRequestDTO,
    CreateJobRequisitionResponseDTO,
    GetJobRequisitionQueryDTO,
    JobRequisitionReadDto,
    LockJobRequisitionRubricRequestDTO,
    LockJobRequisitionRubricResponseDTO,
    PublishJobRequisitionRequestDTO,
    PublishJobRequisitionResponseDTO,
    TierThresholdsDTO,
    UpdateJobRubricRequestDTO,
    UpdateJobRubricResponseDTO,
)
from talent_screener.domain.contexts.recruitment import (
    CompetencyCriterion,
    GatekeeperRule,
    HardSkillGatekeeperSet,
    JobRequisition,
    JobRequisitionRepositoryPort,
    TierThresholdConfig,
)


def _build_criteria_and_gatekeepers(
    criteria_inputs: Sequence[CompetencyCriterionInputDTO],
) -> tuple[list[CompetencyCriterion], HardSkillGatekeeperSet]:
    criteria = [
        CompetencyCriterion(
            name=item.name,
            canonical_skill_id=item.canonical_skill_id,
            weight=item.weight,
            target_depth=item.target_depth,
            is_gatekeeper=item.is_gatekeeper,
            raw_importance=item.raw_importance,
            ideal_anchor_descriptor=item.ideal_anchor_descriptor,
        )
        for item in criteria_inputs
    ]
    gatekeeper_rules = frozenset(
        GatekeeperRule(
            canonical_skill_id=item.canonical_skill_id,
            min_applied_months=item.min_applied_months,
            strict_disqualification=item.strict_disqualification,
        )
        for item in criteria_inputs
        if item.is_gatekeeper
    )
    return criteria, HardSkillGatekeeperSet(rules=gatekeeper_rules)


def _to_thresholds(dto: TierThresholdsDTO | None) -> TierThresholdConfig | None:
    if dto is None:
        return None
    return TierThresholdConfig(
        fast_track_threshold=dto.fast_track_threshold,
        interview_threshold=dto.interview_threshold,
        review_threshold=dto.review_threshold,
    )


class CreateJobRequisitionUseCase:
    """Creates and persists a draft JobRequisition with validated rubric and gatekeepers."""

    def __init__(
        self,
        *,
        requisition_repo: JobRequisitionRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._requisition_repo = requisition_repo
        self._uow = uow

    async def execute(
        self, request: CreateJobRequisitionRequestDTO
    ) -> CreateJobRequisitionResponseDTO:
        criteria, gatekeepers = _build_criteria_and_gatekeepers(request.criteria)
        requisition = JobRequisition(
            tenant_id=request.tenant_id,
            title=request.title,
            description=request.description,
            seniority_level=request.seniority_level,
            criteria=criteria,
            gatekeepers=gatekeepers,
            tier_thresholds=_to_thresholds(request.tier_thresholds),
            interview_timebox_minutes=request.interview_timebox_minutes,
        )
        async with self._uow:
            await self._uow.begin(request.tenant_id)
            self._requisition_repo.save(requisition)
            self._uow.register_aggregate(requisition)
            await self._uow.commit()

        return CreateJobRequisitionResponseDTO(
            requisition_id=requisition.id,
            tenant_id=requisition.tenant_id,
            status=requisition.status,
            rubric_version=int(requisition.rubric_version),
            rubric_version_id=requisition.rubric_version_id,
        )


class PublishJobRequisitionUseCase:
    """Transitions a JobRequisition to PUBLISHED and commits JobRequisitionPublishedEvent."""

    def __init__(
        self,
        *,
        requisition_repo: JobRequisitionRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._requisition_repo = requisition_repo
        self._uow = uow

    async def execute(
        self, request: PublishJobRequisitionRequestDTO
    ) -> PublishJobRequisitionResponseDTO:
        async with self._uow:
            await self._uow.begin(request.tenant_id)
            requisition = self._requisition_repo.get_by_id(
                request.tenant_id, request.requisition_id
            )
            if requisition is None:
                raise ResourceNotFoundError(
                    f"JobRequisition {request.requisition_id} not found.",
                    code="REQUISITION_NOT_FOUND",
                )
            requisition.publish()
            self._requisition_repo.save(requisition)
            self._uow.register_aggregate(requisition)
            await self._uow.commit()

        return PublishJobRequisitionResponseDTO(
            requisition_id=requisition.id,
            status=requisition.status,
            rubric_version=int(requisition.rubric_version),
            rubric_version_id=requisition.rubric_version_id,
        )


class UpdateJobRubricUseCase:
    """Updates competency criteria and thresholds when the rubric is not locked."""

    def __init__(
        self,
        *,
        requisition_repo: JobRequisitionRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._requisition_repo = requisition_repo
        self._uow = uow

    async def execute(self, request: UpdateJobRubricRequestDTO) -> UpdateJobRubricResponseDTO:
        async with self._uow:
            await self._uow.begin(request.tenant_id)
            requisition = self._requisition_repo.get_by_id(
                request.tenant_id, request.requisition_id
            )
            if requisition is None:
                raise ResourceNotFoundError(
                    f"JobRequisition {request.requisition_id} not found.",
                    code="REQUISITION_NOT_FOUND",
                )
            criteria, gatekeepers = _build_criteria_and_gatekeepers(request.criteria)
            requisition.update_rubric(
                criteria=criteria,
                gatekeepers=gatekeepers,
                tier_thresholds=_to_thresholds(request.tier_thresholds),
            )
            self._requisition_repo.save(requisition)
            self._uow.register_aggregate(requisition)
            await self._uow.commit()

        return UpdateJobRubricResponseDTO(
            requisition_id=requisition.id,
            rubric_version=int(requisition.rubric_version),
            rubric_version_id=requisition.rubric_version_id,
            competency_count=len(requisition.criteria),
        )


class LockJobRequisitionRubricUseCase:
    """Locks a JobRequisition rubric when a ScreeningBatch or InterviewSession starts."""

    def __init__(
        self,
        *,
        requisition_repo: JobRequisitionRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._requisition_repo = requisition_repo
        self._uow = uow

    async def execute(
        self, request: LockJobRequisitionRubricRequestDTO
    ) -> LockJobRequisitionRubricResponseDTO:
        async with self._uow:
            await self._uow.begin(request.tenant_id)
            requisition = self._requisition_repo.get_by_id(
                request.tenant_id, request.requisition_id
            )
            if requisition is None:
                raise ResourceNotFoundError(
                    f"JobRequisition {request.requisition_id} not found.",
                    code="REQUISITION_NOT_FOUND",
                )
            if request.lock_source_type == "SCREENING_BATCH":
                requisition.register_active_screening_batch(request.lock_source_id)
            else:
                requisition.register_active_interview_session(request.lock_source_id)
            self._requisition_repo.save(requisition)
            self._uow.register_aggregate(requisition)
            await self._uow.commit()

        return LockJobRequisitionRubricResponseDTO(
            requisition_id=requisition.id,
            is_rubric_locked=requisition.is_rubric_locked,
            active_batch_count=len(requisition.active_screening_batch_ids),
            active_session_count=len(requisition.active_interview_session_ids),
        )


class ArchiveJobRequisitionUseCase:
    """Archives an active or draft JobRequisition and publishes archival event."""

    def __init__(
        self,
        *,
        requisition_repo: JobRequisitionRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._requisition_repo = requisition_repo
        self._uow = uow

    async def execute(
        self, request: ArchiveJobRequisitionRequestDTO
    ) -> ArchiveJobRequisitionResponseDTO:
        async with self._uow:
            await self._uow.begin(request.tenant_id)
            requisition = self._requisition_repo.get_by_id(
                request.tenant_id, request.requisition_id
            )
            if requisition is None:
                raise ResourceNotFoundError(
                    f"JobRequisition {request.requisition_id} not found.",
                    code="REQUISITION_NOT_FOUND",
                )
            requisition.archive()
            self._requisition_repo.save(requisition)
            self._uow.register_aggregate(requisition)
            await self._uow.commit()

        return ArchiveJobRequisitionResponseDTO(
            requisition_id=requisition.id,
            status=requisition.status,
        )


class GetJobRequisitionUseCase:
    """Read-only query interactor projecting a JobRequisition into an immutable Read DTO."""

    def __init__(self, *, requisition_repo: JobRequisitionRepositoryPort) -> None:
        self._requisition_repo = requisition_repo

    async def execute(self, query: GetJobRequisitionQueryDTO) -> JobRequisitionReadDto:
        requisition = self._requisition_repo.get_by_id(query.tenant_id, query.requisition_id)
        if requisition is None:
            raise ResourceNotFoundError(
                f"JobRequisition {query.requisition_id} not found.",
                code="REQUISITION_NOT_FOUND",
            )
        return JobRequisitionReadDto(
            requisition_id=requisition.id,
            tenant_id=requisition.tenant_id,
            title=requisition.title,
            description=requisition.description,
            seniority_level=requisition.seniority_level,
            status=requisition.status,
            rubric_version=int(requisition.rubric_version),
            rubric_version_id=requisition.rubric_version_id,
            is_rubric_locked=requisition.is_rubric_locked,
            interview_timebox_minutes=requisition.interview_timebox_minutes,
            tier_thresholds=TierThresholdsDTO(
                fast_track_threshold=requisition.tier_thresholds.fast_track_threshold,
                interview_threshold=requisition.tier_thresholds.interview_threshold,
                review_threshold=requisition.tier_thresholds.review_threshold,
            ),
            criteria=tuple(
                CompetencyCriterionReadDTO(
                    criterion_id=c.id,
                    name=c.name,
                    canonical_skill_id=c.canonical_skill_id,
                    weight=c.weight,
                    target_depth=c.target_depth,
                    is_gatekeeper=c.is_gatekeeper,
                    ideal_anchor_descriptor=c.ideal_anchor_descriptor,
                )
                for c in requisition.criteria
            ),
            gatekeeper_skills=requisition.gatekeepers.skill_ids,
            aggregate_version=int(requisition.version),
        )
