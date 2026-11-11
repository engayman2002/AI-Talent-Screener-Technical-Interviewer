"""REST Controller for Live Interview Management endpoints."""

from __future__ import annotations

from talent_screener.application.live_interview.ports.inbound import (
    CompleteInterviewSessionPort,
    InitializeInterviewSessionPort,
    RecordClientTelemetryPort,
    TerminateInterviewEarlyPort,
)
from talent_screener.presentation.middleware.auth import PrincipalContext
from talent_screener.presentation.schemas import (
    ApiResponseEnvelope,
    HttpRequest,
    HttpResponse,
    ResponseMeta,
    parse_complete_interview,
    parse_initialize_interview,
    parse_record_telemetry,
    parse_terminate_interview,
    parse_uuid,
)


class LiveInterviewController:
    """HTTP transport adapter invoking Live Interview Inbound Driving Ports."""

    def __init__(
        self,
        *,
        initialize_interview: InitializeInterviewSessionPort,
        record_telemetry: RecordClientTelemetryPort,
        terminate_interview: TerminateInterviewEarlyPort,
        complete_interview: CompleteInterviewSessionPort,
    ) -> None:
        self._initialize = initialize_interview
        self._record_telemetry = record_telemetry
        self._terminate = terminate_interview
        self._complete = complete_interview

    async def initialize(
        self, request: HttpRequest, principal: PrincipalContext, meta: ResponseMeta
    ) -> HttpResponse:
        cmd = parse_initialize_interview(request.body, tenant_id=principal.tenant_id)
        outcome = await self._initialize.execute(cmd)
        return HttpResponse(
            status_code=201,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def record_telemetry(
        self,
        request: HttpRequest,
        session_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        session_id = parse_uuid(session_id_raw, field_name="session_id")
        cmd = parse_record_telemetry(
            request.body, tenant_id=principal.tenant_id, session_id=session_id
        )
        outcome = await self._record_telemetry.execute(cmd)
        return HttpResponse(
            status_code=202,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def terminate(
        self,
        request: HttpRequest,
        session_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        session_id = parse_uuid(session_id_raw, field_name="session_id")
        cmd = parse_terminate_interview(
            request.body, tenant_id=principal.tenant_id, session_id=session_id
        )
        outcome = await self._terminate.execute(cmd)
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def complete(
        self,
        request: HttpRequest,
        session_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        session_id = parse_uuid(session_id_raw, field_name="session_id")
        cmd = parse_complete_interview(
            request.body, tenant_id=principal.tenant_id, session_id=session_id
        )
        outcome = await self._complete.execute(cmd)
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )
