"""Unit tests for Phase 5 Presentation REST API, Middleware & RFC 9457 Error Translator."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from uuid import UUID, uuid4

import pytest

from talent_screener.application.common.exceptions import ExternalAdapterFailureError
from talent_screener.composition_root import build_phase5_container
from talent_screener.domain.contexts.identity_governance import ConsentScope
from talent_screener.domain.shared import DomainError, SHA256Hash, TenantId, generate_uuid7
from talent_screener.presentation.middleware import (
    AuthenticationError,
    BearerTokenAuthenticator,
    ExceptionToProblemDetailsTranslator,
    RbacScope,
)
from talent_screener.presentation.schemas import HttpRequest


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


ALL_ADMIN_SCOPES = (
    RbacScope.RECRUITMENT_WRITE,
    RbacScope.RECRUITMENT_READ,
    RbacScope.SCREENING_WRITE,
    RbacScope.SCREENING_READ,
    RbacScope.INTERVIEW_CANDIDATE,
    RbacScope.INTERVIEW_MANAGE,
    RbacScope.EVALUATION_READ,
    RbacScope.EVALUATION_OVERRIDE,
    RbacScope.GOVERNANCE_ADMIN,
)


def test_rest_api_full_catalog_idempotency_auth_rbac_cross_tenant_and_rate_limit() -> None:
    async def _run_test() -> None:
        now = 1_700_000_000.0
        bundle = build_phase5_container(
            rate_limit_default_per_minute=200,
            bucket_rate_limits={"recruitment_read": 2},
            clock_fn=lambda: now,
        )
        router = bundle.rest_router
        auth = bundle.authenticator
        tenant_id = TenantId.generate()
        other_tenant = TenantId.generate()
        actor_id = generate_uuid7()

        admin_token = auth.issue_token(
            tenant_id=tenant_id,
            actor_id=actor_id,
            scopes=ALL_ADMIN_SCOPES,
            ttl_seconds=3600,
        )
        read_only_token = auth.issue_token(
            tenant_id=tenant_id,
            actor_id=actor_id,
            scopes=(RbacScope.RECRUITMENT_READ,),
            ttl_seconds=3600,
        )
        admin_hdr = {"Authorization": f"Bearer {admin_token}"}
        read_hdr = {"Authorization": f"Bearer {read_only_token}"}

        # 1. Missing/invalid auth -> 401 RFC 9457 with WWW-Authenticate
        unauth = await router.handle(HttpRequest(method="GET", path=f"/v1/requisitions/{uuid4()}"))
        assert unauth.status_code == 401
        assert unauth.headers["WWW-Authenticate"] == "Bearer"
        assert unauth.body is not None and unauth.body["error_code"] == "MISSING_AUTH_HEADER"

        # 2. Missing RBAC scope -> 403
        forbidden_rbac = await router.handle(
            HttpRequest(
                method="POST",
                path="/v1/requisitions",
                headers=read_hdr,
                body={"title": "Blocked"},
            )
        )
        assert forbidden_rbac.status_code == 403
        assert forbidden_rbac.body is not None
        assert forbidden_rbac.body["error_code"] == "INSUFFICIENT_RBAC_SCOPE"

        # 3. Cross-tenant body rejection -> 403
        cross_tenant = await router.handle(
            HttpRequest(
                method="POST",
                path="/v1/tenants",
                headers=admin_hdr,
                body={
                    "tenant_id": str(other_tenant.value),
                    "organization_slug": "rogue-tenant",
                    "display_name": "Rogue",
                },
            )
        )
        assert cross_tenant.status_code == 403
        assert cross_tenant.body is not None
        assert cross_tenant.body["error_code"] == "CROSS_TENANT_ACCESS_DENIED"

        # 4. Provision Tenant + Consent via REST (201)
        prov_res = await router.handle(
            HttpRequest(
                method="POST",
                path="/v1/tenants",
                headers=admin_hdr,
                body={
                    "tenant_id": str(tenant_id.value),
                    "organization_slug": "phase5-org",
                    "display_name": "Phase 5 Enterprise",
                },
            )
        )
        assert prov_res.status_code == 201

        # 5. Idempotency replay & payload mismatch conflict (409)
        req_body: dict[str, object] = {
            "title": "Staff Backend Architect",
            "description": "Build event-sourced Python systems with Kafka.",
            "seniority_level": "STAFF",
            "criteria": [
                {
                    "name": "Python & Distributed Systems",
                    "canonical_skill_id": "SKILL_LANG_PYTHON",
                    "weight": 1.0,
                    "target_depth": 4,
                    "is_gatekeeper": True,
                    "min_applied_months": 24,
                }
            ],
        }
        create_res_1 = await router.handle(
            HttpRequest(
                method="POST",
                path="/v1/requisitions",
                headers={**admin_hdr, "Idempotency-Key": "create-req-001"},
                body=req_body,
            )
        )
        assert create_res_1.status_code == 201
        assert create_res_1.headers["X-Idempotency-Replayed"] == "false"

        create_res_2 = await router.handle(
            HttpRequest(
                method="POST",
                path="/v1/requisitions",
                headers={**admin_hdr, "Idempotency-Key": "create-req-001"},
                body=req_body,
            )
        )
        assert create_res_2.status_code == 201
        assert create_res_2.headers["X-Idempotency-Replayed"] == "true"
        assert create_res_2.body == create_res_1.body

        # Mismatched body with same Idempotency-Key -> 409 Conflict
        mismatch_res = await router.handle(
            HttpRequest(
                method="POST",
                path="/v1/requisitions",
                headers={**admin_hdr, "Idempotency-Key": "create-req-001"},
                body={**req_body, "title": "Altered Title"},
            )
        )
        assert mismatch_res.status_code == 409
        assert mismatch_res.body is not None
        assert mismatch_res.body["status"] == 409

        req_data = create_res_1.body["data"]  # type: ignore[index]
        assert isinstance(req_data, dict)
        req_id = str(req_data["requisition_id"])

        # Update rubric -> Publish -> Archive lifecycle (using /api/v1 alias for coverage)
        upd_res = await router.handle(
            HttpRequest(
                method="PUT",
                path=f"/api/v1/requisitions/{req_id}/rubric",
                headers=admin_hdr,
                body={
                    "criteria": req_body["criteria"],
                    "tier_thresholds": {
                        "fast_track_threshold": 85.0,
                        "interview_threshold": 72.0,
                        "review_threshold": 55.0,
                    },
                },
            )
        )
        assert upd_res.status_code == 200

        pub_res = await router.handle(
            HttpRequest(method="POST", path=f"/v1/requisitions/{req_id}/publish", headers=admin_hdr)
        )
        assert pub_res.status_code == 200

        # Rate-limit bucket exhaustion on recruitment_read (limit=2) -> 2 OK then 429
        get1 = await router.handle(
            HttpRequest(method="GET", path=f"/v1/requisitions/{req_id}", headers=read_hdr)
        )
        assert get1.status_code == 200
        assert get1.headers["X-RateLimit-Remaining"] == "1"
        get2 = await router.handle(
            HttpRequest(method="GET", path=f"/v1/requisitions/{req_id}", headers=read_hdr)
        )
        assert get2.status_code == 200
        assert get2.headers["X-RateLimit-Remaining"] == "0"
        get3 = await router.handle(
            HttpRequest(method="GET", path=f"/v1/requisitions/{req_id}", headers=read_hdr)
        )
        assert get3.status_code == 429
        assert int(get3.headers["Retry-After"]) >= 1
        assert get3.body is not None and get3.body["error_code"] == "RATE_LIMIT_EXCEEDED"

        # Invalid enum & path traversal filename validation -> 400 Bad Request
        bad_req = await router.handle(
            HttpRequest(
                method="POST",
                path="/v1/requisitions",
                headers=admin_hdr,
                body={**req_body, "seniority_level": "WIZARD"},
            )
        )
        assert bad_req.status_code == 400
        assert bad_req.body is not None and bad_req.body["status"] == 400

        bad_file = await router.handle(
            HttpRequest(
                method="POST",
                path="/v1/screening-batches",
                headers={**admin_hdr, "Idempotency-Key": "bad-traversal"},
                body={
                    "requisition_id": req_id,
                    "batch_label": "bad",
                    "files": [
                        {
                            "filename": "../secrets.pdf",
                            "sha256_hex": SHA256Hash.from_text("x").value,
                            "file_size_bytes": 2048,
                            "mime_type": "application/pdf",
                        }
                    ],
                },
            )
        )
        assert bad_file.status_code == 400

        # Unknown route -> 404 & wrong verb -> 405
        missing_route = await router.handle(
            HttpRequest(method="GET", path="/v1/nonexistent", headers=admin_hdr)
        )
        assert missing_route.status_code == 404
        wrong_verb = await router.handle(
            HttpRequest(method="DELETE", path=f"/v1/requisitions/{req_id}", headers=admin_hdr)
        )
        assert wrong_verb.status_code == 405

        # Consent -> Revoke lifecycle & Archive requisition
        cand_id = str(generate_uuid7())
        consent_res = await router.handle(
            HttpRequest(
                method="POST",
                path="/v1/consents",
                headers=admin_hdr,
                body={
                    "candidate_id": cand_id,
                    "granted_scopes": [ConsentScope.AI_SCREENING.value],
                },
            )
        )
        assert consent_res.status_code == 201
        consent_id = str(consent_res.body["data"]["record_id"])  # type: ignore[index]

        revoke_res = await router.handle(
            HttpRequest(
                method="POST", path=f"/v1/consents/{consent_id}/revoke", headers=admin_hdr
            )
        )
        assert revoke_res.status_code == 200

        arch_res = await router.handle(
            HttpRequest(method="POST", path=f"/v1/requisitions/{req_id}/archive", headers=admin_hdr)
        )
        assert arch_res.status_code == 200

        # Missing candidate/session resources -> 404 & Terminate/Complete validation
        not_found = await router.handle(
            HttpRequest(
                method="POST",
                path=f"/v1/interviews/{uuid4()}/terminate",
                headers=admin_hdr,
                body={"reason": "No session"},
            )
        )
        assert not_found.status_code == 404

    _run(_run_test())


def test_bearer_authenticator_edge_cases_and_problem_details_translator() -> None:
    now = 1000.0
    auth = BearerTokenAuthenticator(
        signing_key=b"0123456789abcdef0123456789abcdef", clock_fn=lambda: now
    )
    tenant = TenantId.generate()
    actor = UUID(int=42)
    token = auth.issue_token(
        tenant_id=tenant, actor_id=actor, scopes=("recruitment:read",), ttl_seconds=60
    )
    principal = auth.authenticate(f"Bearer {token}")
    assert principal.tenant_id == tenant
    assert principal.actor_id == actor

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(AuthenticationError, match="INVALID_JWT_SIGNATURE"):
        auth.verify_raw_token(tampered)

    now += 120.0
    with pytest.raises(AuthenticationError, match="TOKEN_EXPIRED"):
        auth.verify_raw_token(token)

    translator = ExceptionToProblemDetailsTranslator()
    p503 = translator.translate(
        ExternalAdapterFailureError("LLM timeout", code="UPSTREAM_TIMEOUT", retryable=True),
        instance="/v1/interviews",
        trace_id="tr-1",
    )
    assert p503.status == 503
    assert p503.retry_after_seconds == 5

    p502 = translator.translate(
        ExternalAdapterFailureError("Bad schema", code="BAD_SCHEMA", retryable=False),
        instance="/v1/interviews",
        trace_id="tr-2",
    )
    assert p502.status == 502

    p_domain = translator.translate(
        DomainError("Generic domain error"), instance="/v1/x", trace_id="tr-3"
    )
    assert p_domain.status == 400

    p_unknown = translator.translate(RuntimeError("secret"), instance="/v1/x", trace_id="tr-4")
    assert p_unknown.status == 500
    assert "secret" not in p_unknown.detail
