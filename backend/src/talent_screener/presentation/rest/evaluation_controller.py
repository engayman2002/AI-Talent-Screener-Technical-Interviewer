"""REST Controller for Evaluation & Analytics endpoints."""

from __future__ import annotations

from talent_screener.application.evaluation_analytics.ports.inbound import (
    BuildCandidateComparisonMatrixPort,
    GenerateEvaluationReportPort,
    RecordRecruiterOverridePort,
)
from talent_screener.presentation.middleware.auth import PrincipalContext
from talent_screener.presentation.schemas import (
    ApiResponseEnvelope,
    HttpRequest,
    HttpResponse,
    ResponseMeta,
    parse_comparison_matrix,
    parse_generate_report,
    parse_recruiter_override,
    parse_uuid,
)


class EvaluationController:
    """HTTP transport adapter invoking Evaluation & Analytics Inbound Ports."""

    def __init__(
        self,
        *,
        generate_report: GenerateEvaluationReportPort,
        record_override: RecordRecruiterOverridePort,
        build_comparison_matrix: BuildCandidateComparisonMatrixPort,
    ) -> None:
        self._generate_report = generate_report
        self._record_override = record_override
        self._build_matrix = build_comparison_matrix

    async def generate_report(
        self, request: HttpRequest, principal: PrincipalContext, meta: ResponseMeta
    ) -> HttpResponse:
        cmd = parse_generate_report(request.body, tenant_id=principal.tenant_id)
        outcome = await self._generate_report.execute(cmd)
        return HttpResponse(
            status_code=201,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def record_override(
        self,
        request: HttpRequest,
        report_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        report_id = parse_uuid(report_id_raw, field_name="report_id")
        cmd = parse_recruiter_override(
            request.body,
            tenant_id=principal.tenant_id,
            report_id=report_id,
            principal_actor_id=principal.actor_id,
        )
        outcome = await self._record_override.execute(cmd)
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def comparison_matrix(
        self,
        request: HttpRequest,
        requisition_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        req_id = parse_uuid(requisition_id_raw, field_name="requisition_id")
        cmd = parse_comparison_matrix(
            request.body, tenant_id=principal.tenant_id, requisition_id=req_id
        )
        outcome = await self._build_matrix.execute(cmd)
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )
