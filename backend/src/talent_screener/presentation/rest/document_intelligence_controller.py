"""REST & SSE Streaming Controller for Document Intelligence & Matching endpoints."""

from __future__ import annotations

import json

from talent_screener.application.common.dtos import SequencedProgressEventDTO
from talent_screener.application.common.ports.outbound import RealtimeProgressStreamPort
from talent_screener.application.document_intelligence.dtos import GetScreeningBatchStatusQueryDTO
from talent_screener.application.document_intelligence.ports.inbound import (
    ExecuteHybridMatchingPort,
    GetScreeningBatchStatusPort,
    InitiateScreeningBatchPort,
)
from talent_screener.presentation.exceptions import RequestValidationError
from talent_screener.presentation.middleware.auth import PrincipalContext
from talent_screener.presentation.schemas import (
    ApiResponseEnvelope,
    FieldViolation,
    HttpRequest,
    HttpResponse,
    ResponseMeta,
    parse_execute_matching,
    parse_initiate_batch,
    parse_uuid,
    serialize_value,
)


def format_sse_frame(event: SequencedProgressEventDTO) -> str:
    """Serialize one SequencedProgressEventDTO into standard Server-Sent Events wire syntax."""
    data_payload = {
        "event_id": event.event_id,
        "tenant_id": str(event.tenant_id.value),
        "channel": event.channel,
        "event_type": event.event_type,
        "payload": serialize_value(event.payload),
        "emitted_at": event.emitted_at.isoformat(),
    }
    encoded = json.dumps(data_payload, separators=(",", ":"), sort_keys=True)
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {encoded}\n\n"


class DocumentIntelligenceController:
    """HTTP + SSE transport adapter for CV Screening Batches and Hybrid Matching."""

    def __init__(
        self,
        *,
        initiate_batch: InitiateScreeningBatchPort,
        get_batch_status: GetScreeningBatchStatusPort,
        execute_matching: ExecuteHybridMatchingPort,
        progress_stream: RealtimeProgressStreamPort,
    ) -> None:
        self._initiate_batch = initiate_batch
        self._get_batch_status = get_batch_status
        self._execute_matching = execute_matching
        self._progress_stream = progress_stream

    async def initiate_batch(
        self, request: HttpRequest, principal: PrincipalContext, meta: ResponseMeta
    ) -> HttpResponse:
        cmd = parse_initiate_batch(
            request.body,
            tenant_id=principal.tenant_id,
            header_idempotency_key=request.header("Idempotency-Key"),
        )
        outcome = await self._initiate_batch.execute(cmd)
        envelope = ApiResponseEnvelope(data=outcome, meta=meta).to_dict()
        data_obj = envelope.get("data")
        if isinstance(data_obj, dict):
            data_obj["sse_stream_uri"] = f"/v1/screening-batches/{outcome.batch_id}/events"
        return HttpResponse(status_code=202, body=envelope)

    async def get_batch_status(
        self,
        batch_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        batch_id = parse_uuid(batch_id_raw, field_name="batch_id")
        outcome = await self._get_batch_status.execute(
            GetScreeningBatchStatusQueryDTO(tenant_id=principal.tenant_id, batch_id=batch_id)
        )
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def stream_batch_events(
        self,
        request: HttpRequest,
        batch_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        batch_id = parse_uuid(batch_id_raw, field_name="batch_id")
        # Verify batch exists under authenticated tenant scope prior to opening SSE stream
        await self._get_batch_status.execute(
            GetScreeningBatchStatusQueryDTO(tenant_id=principal.tenant_id, batch_id=batch_id)
        )

        raw_last_id = request.header("Last-Event-ID") or request.query_params.get("last_event_id")
        last_event_id: int | None = None
        if raw_last_id is not None and raw_last_id.strip():
            try:
                last_event_id = int(raw_last_id.strip())
                if last_event_id < 0:
                    raise ValueError("negative")
            except ValueError as exc:
                raise RequestValidationError(
                    "Invalid Last-Event-ID; must be a non-negative integer.",
                    errors=(
                        FieldViolation(
                            field_path="Last-Event-ID",
                            constraint="non_negative_integer",
                            reason=f"Got {raw_last_id!r}",
                        ),
                    ),
                ) from exc

        channel = f"batch:{batch_id}"
        events = self._progress_stream.replay_since(
            tenant_id=principal.tenant_id,
            channel=channel,
            last_event_id=last_event_id,
        )
        sse_wire_text = "".join(format_sse_frame(ev) for ev in events)
        body = ApiResponseEnvelope(
            data={
                "channel": channel,
                "replayed_after_event_id": last_event_id,
                "event_count": len(events),
                "events": [serialize_value(ev) for ev in events],
            },
            meta=meta,
        ).to_dict()
        return HttpResponse(
            status_code=200,
            body=body,
            raw_text=sse_wire_text,
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    async def execute_matching(
        self,
        request: HttpRequest,
        requisition_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        req_id = parse_uuid(requisition_id_raw, field_name="requisition_id")
        cmd = parse_execute_matching(
            request.body, tenant_id=principal.tenant_id, requisition_id=req_id
        )
        outcome = await self._execute_matching.execute(cmd)
        return HttpResponse(
            status_code=201,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )
