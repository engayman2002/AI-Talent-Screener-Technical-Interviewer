"""REST Controller for Identity, Consent & GDPR Cryptographic Erasure endpoints."""

from __future__ import annotations

from talent_screener.application.identity_governance.ports.inbound import (
    ExecuteCandidateErasurePort,
    ProvisionTenantAccountPort,
    RecordCandidateConsentPort,
    RevokeCandidateConsentPort,
)
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.presentation.middleware.auth import PrincipalContext
from talent_screener.presentation.schemas import (
    ApiResponseEnvelope,
    HttpRequest,
    HttpResponse,
    ResponseMeta,
    parse_erasure,
    parse_provision_tenant,
    parse_record_consent,
    parse_revoke_consent,
    parse_uuid,
)


class IdentityController:
    """HTTP transport adapter invoking Identity & Governance Inbound Ports."""

    def __init__(
        self,
        *,
        provision_tenant: ProvisionTenantAccountPort,
        record_consent: RecordCandidateConsentPort,
        revoke_consent: RevokeCandidateConsentPort,
        execute_erasure: ExecuteCandidateErasurePort,
    ) -> None:
        self._provision_tenant = provision_tenant
        self._record_consent = record_consent
        self._revoke_consent = revoke_consent
        self._execute_erasure = execute_erasure

    async def provision_tenant(
        self, request: HttpRequest, principal: PrincipalContext, meta: ResponseMeta
    ) -> HttpResponse:
        explicit_tenant: TenantId | None = None
        if request.body is not None and request.body.get("tenant_id") is not None:
            explicit_tenant = TenantId(
                parse_uuid(request.body["tenant_id"], field_name="tenant_id")
            )
        cmd = parse_provision_tenant(request.body, tenant_id=explicit_tenant)
        outcome = await self._provision_tenant.execute(cmd)
        return HttpResponse(
            status_code=201,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def record_consent(
        self, request: HttpRequest, principal: PrincipalContext, meta: ResponseMeta
    ) -> HttpResponse:
        cmd = parse_record_consent(request.body, tenant_id=principal.tenant_id)
        outcome = await self._record_consent.execute(cmd)
        return HttpResponse(
            status_code=201,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def revoke_consent(
        self,
        consent_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        consent_id = parse_uuid(consent_id_raw, field_name="consent_id")
        cmd = parse_revoke_consent(tenant_id=principal.tenant_id, consent_id=consent_id)
        outcome = await self._revoke_consent.execute(cmd)
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )

    async def erase_candidate_pii(
        self,
        request: HttpRequest,
        candidate_id_raw: str,
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse:
        cand_id = parse_uuid(candidate_id_raw, field_name="candidate_id")
        cmd = parse_erasure(request.body, tenant_id=principal.tenant_id, candidate_id=cand_id)
        outcome = await self._execute_erasure.execute(cmd)
        return HttpResponse(
            status_code=200,
            body=ApiResponseEnvelope(data=outcome, meta=meta).to_dict(),
        )
