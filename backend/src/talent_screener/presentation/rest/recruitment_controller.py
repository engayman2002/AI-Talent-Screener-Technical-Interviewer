"""REST Controller for Recruitment Management endpoints."""

from __future__ import annotations

from talent_screener.application.recruitment.dtos import (
    ArchiveJobRequisitionRequestDTO,
    GetJobRequisitionQueryDTO,
    PublishJobRequisitionRequestDTO,
)
from talent_screener.application.recruitment.ports.inbound import (
    ArchiveJobRequisitionPort,
    CreateJobRequisitionPort,
    GetJobRequisitionPort,
    PublishJobRequisitionPort,
    UpdateJobRubricPort,
)
from talent_screener.presentation.middleware.auth import PrincipalContext
from talent_screener.presentation.schemas import (
    ApiResponseEnvelope,
    HttpRequest,
    HttpResponse,
    ResponseMeta,
    parse_create_requisition,
    parse_update_rubric,
    parse_uuid,
)


class RecruitmentController:
    """HTTP transport adapter invoking Recruitment Inbound Driving Ports."""

    def __init__(
        self,
        *,
        create_requisition: CreateJobRequisitionPort,
        publish_requisition: PublishJobRequisitionPort,
        update_rubric: UpdateJobRubricPort,
        archive_requisition: ArchiveJobRequisitionPort,
        get_requisition: GetJobRequisitionPort,
    ) -> None:
        self._create = create_requisition
        self._publish = publish_requisition
        self._update_rubric = update_rubric
        self._archive = archive_requisition
        self._get = get_requisition

    async def create(
        self, request: HttpRequest, principal: PrincipalContext, meta: ResponseMeta
    ) -> HttpResponse:
        cmd = parse_create_requisition(request.body, tenant_id=principal.tenant_id)
        outcome = await self._create.execute(cmd)
        return HttpResponse(
            status_code=201,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def publish(
        self,
        requisition_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        req_id = parse_uuid(requisition_id_raw, field_name="requisition_id")
        outcome = await self._publish.execute(
            PublishJobRequisitionRequestDTO(tenant_id=principal.tenant_id, requisition_id=req_id)
        )
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def update_rubric(
        self,
        request: HttpRequest,
        requisition_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        req_id = parse_uuid(requisition_id_raw, field_name="requisition_id")
        cmd = parse_update_rubric(
            request.body, tenant_id=principal.tenant_id, requisition_id=req_id
        )
        outcome = await self._update_rubric.execute(cmd)
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def archive(
        self,
        requisition_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        req_id = parse_uuid(requisition_id_raw, field_name="requisition_id")
        outcome = await self._archive.execute(
            ArchiveJobRequisitionRequestDTO(tenant_id=principal.tenant_id, requisition_id=req_id)
        )
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def get_detail(
        self,
        requisition_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        req_id = parse_uuid(requisition_id_raw, field_name="requisition_id")
        outcome = await self._get.execute(
            GetJobRequisitionQueryDTO(tenant_id=principal.tenant_id, requisition_id=req_id)
        )
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )
