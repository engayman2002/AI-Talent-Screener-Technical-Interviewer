"""Unit tests for Recruitment Management Application Use Cases."""

from __future__ import annotations

import pytest

from talent_screener.application.common.exceptions import ResourceNotFoundError
from talent_screener.application.recruitment.dtos import (
    ArchiveJobRequisitionRequestDTO,
    CompetencyCriterionInputDTO,
    CreateJobRequisitionRequestDTO,
    GetJobRequisitionQueryDTO,
    LockJobRequisitionRubricRequestDTO,
    PublishJobRequisitionRequestDTO,
    TierThresholdsDTO,
    UpdateJobRubricRequestDTO,
)
from talent_screener.domain.contexts.recruitment import (
    RequisitionStatus,
    RubricLockedError,
    SeniorityLevel,
)
from talent_screener.domain.shared import TenantId, generate_uuid7
from tests.unit.application.fakes import build_test_harness, run_async


def _criteria() -> tuple[CompetencyCriterionInputDTO, ...]:
    return (
        CompetencyCriterionInputDTO(
            name="Distributed Systems",
            canonical_skill_id="SKILL_DISTRIBUTED_SYSTEMS",
            weight=0.60,
            target_depth=4,
            is_gatekeeper=True,
            min_applied_months=24,
        ),
        CompetencyCriterionInputDTO(
            name="Python Async",
            canonical_skill_id="SKILL_LANG_PYTHON",
            weight=0.40,
            target_depth=4,
            is_gatekeeper=True,
            min_applied_months=24,
        ),
    )


@pytest.mark.unit
def test_recruitment_use_cases_lifecycle_rubric_locking_and_read_dto() -> None:
    async def _run() -> None:
        h = build_test_harness()
        c = h.container
        tid = TenantId.generate()

        created = await c.create_job_requisition.execute(
            CreateJobRequisitionRequestDTO(
                tenant_id=tid,
                title="Staff Distributed Systems Engineer",
                description="Architect distributed screening & adaptive interviewing services.",
                seniority_level=SeniorityLevel.STAFF,
                criteria=_criteria(),
                tier_thresholds=TierThresholdsDTO(88.0, 75.0, 58.0),
            )
        )
        assert created.status is RequisitionStatus.DRAFT
        assert created.rubric_version == 1

        published = await c.publish_job_requisition.execute(
            PublishJobRequisitionRequestDTO(tenant_id=tid, requisition_id=created.requisition_id)
        )
        assert published.status is RequisitionStatus.PUBLISHED

        updated = await c.update_job_rubric.execute(
            UpdateJobRubricRequestDTO(
                tenant_id=tid,
                requisition_id=created.requisition_id,
                criteria=_criteria(),
            )
        )
        assert updated.rubric_version == 2

        read_dto = await c.get_job_requisition.execute(
            GetJobRequisitionQueryDTO(tenant_id=tid, requisition_id=created.requisition_id)
        )
        assert read_dto.title == "Staff Distributed Systems Engineer"
        assert not read_dto.is_rubric_locked
        assert "SKILL_DISTRIBUTED_SYSTEMS" in read_dto.gatekeeper_skills

        # Lock via active batch -> subsequent rubric update fails with RubricLockedError
        lock_res = await c.lock_job_rubric.execute(
            LockJobRequisitionRubricRequestDTO(
                tenant_id=tid,
                requisition_id=created.requisition_id,
                lock_source_type="SCREENING_BATCH",
                lock_source_id=generate_uuid7(),
            )
        )
        assert lock_res.is_rubric_locked
        assert lock_res.active_batch_count == 1

        with pytest.raises(RubricLockedError):
            await c.update_job_rubric.execute(
                UpdateJobRubricRequestDTO(
                    tenant_id=tid,
                    requisition_id=created.requisition_id,
                    criteria=_criteria(),
                )
            )

        archived = await c.archive_job_requisition.execute(
            ArchiveJobRequisitionRequestDTO(tenant_id=tid, requisition_id=created.requisition_id)
        )
        assert archived.status is RequisitionStatus.ARCHIVED

        # Missing requisition raises ResourceNotFoundError
        with pytest.raises(ResourceNotFoundError):
            await c.get_job_requisition.execute(
                GetJobRequisitionQueryDTO(tenant_id=tid, requisition_id=generate_uuid7())
            )

    run_async(_run())
