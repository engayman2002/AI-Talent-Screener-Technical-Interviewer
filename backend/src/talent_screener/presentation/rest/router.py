"""Unified Clean-Architecture REST API Router & Middleware Dispatch Pipeline."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.presentation.middleware import (
    AuthenticationError,
    BearerTokenAuthenticator,
    ExceptionToProblemDetailsTranslator,
    IdempotencyMiddleware,
    PrincipalContext,
    RbacScope,
    TenantRateLimiter,
)
from talent_screener.presentation.rest.document_intelligence_controller import (
    DocumentIntelligenceController,
)
from talent_screener.presentation.rest.evaluation_controller import EvaluationController
from talent_screener.presentation.rest.identity_controller import IdentityController
from talent_screener.presentation.rest.live_interview_controller import LiveInterviewController
from talent_screener.presentation.rest.ops_controller import OpsController
from talent_screener.presentation.rest.recruitment_controller import RecruitmentController
from talent_screener.presentation.schemas import HttpRequest, HttpResponse, ResponseMeta

_PUBLIC_HEALTH_TENANT = TenantId.generate()

RouteHandlerFn = Callable[
    [HttpRequest, dict[str, str], PrincipalContext, ResponseMeta],
    Awaitable[HttpResponse],
]


@runtime_checkable
class RestHandlerCallableProtocol(Protocol):
    """Callable route dispatcher contract for ASGI/controller adapters."""

    async def __call__(
        self,
        request: HttpRequest,
        path_params: dict[str, str],
        principal: PrincipalContext,
        meta: ResponseMeta,
    ) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """Declarative route specification binding HTTP verb, regex pattern, RBAC, and handler."""

    method: str
    pattern: re.Pattern[str]
    required_scopes: tuple[str, ...]
    any_of_scopes: bool
    rate_bucket: str
    supports_idempotency: bool
    handler: RouteHandlerFn
    requires_auth: bool = True


def _normalize_v1_path(raw_path: str) -> str:
    stripped = raw_path.split("?", maxsplit=1)[0].rstrip("/") or "/"
    if stripped.startswith("/api/v1/"):
        return stripped.removeprefix("/api")
    if stripped == "/api/v1":
        return "/v1"
    return stripped


class RestApiRouter:
    """Dispatches all 20 v1 REST & SSE endpoints through unified Auth/RBAC/RateLimit/Idempotency."""

    def __init__(
        self,
        *,
        authenticator: BearerTokenAuthenticator,
        rate_limiter: TenantRateLimiter,
        idempotency_middleware: IdempotencyMiddleware,
        recruitment_controller: RecruitmentController,
        document_controller: DocumentIntelligenceController,
        interview_controller: LiveInterviewController,
        evaluation_controller: EvaluationController,
        identity_controller: IdentityController,
        ops_controller: OpsController | None = None,
        error_translator: ExceptionToProblemDetailsTranslator | None = None,
    ) -> None:
        self._auth = authenticator
        self._rate_limiter = rate_limiter
        self._idempotency = idempotency_middleware
        self._ops = ops_controller if ops_controller is not None else OpsController()
        self._translator = (
            error_translator
            if error_translator is not None
            else ExceptionToProblemDetailsTranslator()
        )
        self._routes: tuple[RouteSpec, ...] = self._build_route_table(
            recruitment=recruitment_controller,
            document=document_controller,
            interview=interview_controller,
            evaluation=evaluation_controller,
            identity=identity_controller,
            ops=self._ops,
        )

    @property
    def routes(self) -> tuple[RouteSpec, ...]:
        """Expose registered immutable route specifications."""
        return self._routes

    def _build_route_table(
        self,
        *,
        recruitment: RecruitmentController,
        document: DocumentIntelligenceController,
        interview: LiveInterviewController,
        evaluation: EvaluationController,
        identity: IdentityController,
        ops: OpsController,
    ) -> tuple[RouteSpec, ...]:
        async def _req_create(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = params
            return await recruitment.create(req, p, meta)

        async def _req_publish(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = req
            return await recruitment.publish(params["requisition_id"], p, meta)

        async def _req_rubric(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            return await recruitment.update_rubric(req, params["requisition_id"], p, meta)

        async def _req_archive(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = req
            return await recruitment.archive(params["requisition_id"], p, meta)

        async def _req_get(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = req
            return await recruitment.get_detail(params["requisition_id"], p, meta)

        async def _batch_init(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = params
            return await document.initiate_batch(req, p, meta)

        async def _batch_status(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = req
            return await document.get_batch_status(params["batch_id"], p, meta)

        async def _batch_events(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            return await document.stream_batch_events(req, params["batch_id"], p, meta)

        async def _match_exec(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            return await document.execute_matching(req, params["requisition_id"], p, meta)

        async def _int_init(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = params
            return await interview.initialize(req, p, meta)

        async def _int_tel(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            return await interview.record_telemetry(req, params["session_id"], p, meta)

        async def _int_term(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            return await interview.terminate(req, params["session_id"], p, meta)

        async def _int_comp(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            return await interview.complete(req, params["session_id"], p, meta)

        async def _eval_gen(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = params
            return await evaluation.generate_report(req, p, meta)

        async def _eval_over(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            return await evaluation.record_override(req, params["report_id"], p, meta)

        async def _eval_mat(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            return await evaluation.comparison_matrix(req, params["requisition_id"], p, meta)

        async def _id_tenant(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = params
            return await identity.provision_tenant(req, p, meta)

        async def _id_consent(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = params
            return await identity.record_consent(req, p, meta)

        async def _id_revoke(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = req
            return await identity.revoke_consent(params["consent_id"], p, meta)

        async def _id_erase(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            return await identity.erase_candidate_pii(req, params["candidate_id"], p, meta)

        async def _ops_live(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = (params, p, meta)
            return await ops.liveness(req)

        async def _ops_ready(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = (params, p, meta)
            return await ops.readiness(req)

        async def _ops_metrics(
            req: HttpRequest, params: dict[str, str], p: PrincipalContext, meta: ResponseMeta
        ) -> HttpResponse:
            _ = params
            return await ops.metrics(req, p, meta)

        return (
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/requisitions$"),
                required_scopes=(RbacScope.RECRUITMENT_WRITE,),
                any_of_scopes=False,
                rate_bucket="recruitment_write",
                supports_idempotency=True,
                handler=_req_create,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/requisitions/(?P<requisition_id>[^/]+)/publish$"),
                required_scopes=(RbacScope.RECRUITMENT_WRITE,),
                any_of_scopes=False,
                rate_bucket="recruitment_write",
                supports_idempotency=True,
                handler=_req_publish,
            ),
            RouteSpec(
                method="PUT",
                pattern=re.compile(r"^/v1/requisitions/(?P<requisition_id>[^/]+)/rubric$"),
                required_scopes=(RbacScope.RECRUITMENT_WRITE,),
                any_of_scopes=False,
                rate_bucket="recruitment_write",
                supports_idempotency=True,
                handler=_req_rubric,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/requisitions/(?P<requisition_id>[^/]+)/archive$"),
                required_scopes=(RbacScope.RECRUITMENT_WRITE,),
                any_of_scopes=False,
                rate_bucket="recruitment_write",
                supports_idempotency=True,
                handler=_req_archive,
            ),
            RouteSpec(
                method="GET",
                pattern=re.compile(r"^/v1/requisitions/(?P<requisition_id>[^/]+)$"),
                required_scopes=(RbacScope.RECRUITMENT_READ,),
                any_of_scopes=False,
                rate_bucket="recruitment_read",
                supports_idempotency=False,
                handler=_req_get,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/screening-batches$"),
                required_scopes=(RbacScope.SCREENING_WRITE,),
                any_of_scopes=False,
                rate_bucket="screening_batches",
                supports_idempotency=True,
                handler=_batch_init,
            ),
            RouteSpec(
                method="GET",
                pattern=re.compile(r"^/v1/screening-batches/(?P<batch_id>[^/]+)$"),
                required_scopes=(RbacScope.SCREENING_READ,),
                any_of_scopes=False,
                rate_bucket="screening_read",
                supports_idempotency=False,
                handler=_batch_status,
            ),
            RouteSpec(
                method="GET",
                pattern=re.compile(r"^/v1/screening-batches/(?P<batch_id>[^/]+)/events$"),
                required_scopes=(RbacScope.SCREENING_READ,),
                any_of_scopes=False,
                rate_bucket="screening_sse",
                supports_idempotency=False,
                handler=_batch_events,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/requisitions/(?P<requisition_id>[^/]+)/matches$"),
                required_scopes=(RbacScope.SCREENING_WRITE,),
                any_of_scopes=False,
                rate_bucket="screening_matching",
                supports_idempotency=True,
                handler=_match_exec,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/interviews$"),
                required_scopes=(RbacScope.INTERVIEW_MANAGE,),
                any_of_scopes=False,
                rate_bucket="interview_manage",
                supports_idempotency=True,
                handler=_int_init,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/interviews/(?P<session_id>[^/]+)/telemetry$"),
                required_scopes=(RbacScope.INTERVIEW_CANDIDATE, RbacScope.INTERVIEW_MANAGE),
                any_of_scopes=True,
                rate_bucket="interview_telemetry",
                supports_idempotency=False,
                handler=_int_tel,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/interviews/(?P<session_id>[^/]+)/terminate$"),
                required_scopes=(RbacScope.INTERVIEW_MANAGE, RbacScope.INTERVIEW_CANDIDATE),
                any_of_scopes=True,
                rate_bucket="interview_manage",
                supports_idempotency=True,
                handler=_int_term,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/interviews/(?P<session_id>[^/]+)/complete$"),
                required_scopes=(RbacScope.INTERVIEW_MANAGE,),
                any_of_scopes=False,
                rate_bucket="interview_manage",
                supports_idempotency=True,
                handler=_int_comp,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/evaluations$"),
                required_scopes=(
                    RbacScope.EVALUATION_READ,
                    RbacScope.EVALUATION_OVERRIDE,
                    RbacScope.INTERVIEW_MANAGE,
                ),
                any_of_scopes=True,
                rate_bucket="evaluation_write",
                supports_idempotency=True,
                handler=_eval_gen,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/evaluations/(?P<report_id>[^/]+)/overrides$"),
                required_scopes=(RbacScope.EVALUATION_OVERRIDE,),
                any_of_scopes=False,
                rate_bucket="evaluation_write",
                supports_idempotency=True,
                handler=_eval_over,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(
                    r"^/v1/requisitions/(?P<requisition_id>[^/]+)/comparison-matrix$"
                ),
                required_scopes=(RbacScope.EVALUATION_READ,),
                any_of_scopes=False,
                rate_bucket="evaluation_read",
                supports_idempotency=False,
                handler=_eval_mat,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/tenants$"),
                required_scopes=(RbacScope.GOVERNANCE_ADMIN,),
                any_of_scopes=False,
                rate_bucket="governance_admin",
                supports_idempotency=True,
                handler=_id_tenant,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/consents$"),
                required_scopes=(RbacScope.GOVERNANCE_ADMIN,),
                any_of_scopes=False,
                rate_bucket="governance_admin",
                supports_idempotency=True,
                handler=_id_consent,
            ),
            RouteSpec(
                method="POST",
                pattern=re.compile(r"^/v1/consents/(?P<consent_id>[^/]+)/revoke$"),
                required_scopes=(RbacScope.GOVERNANCE_ADMIN,),
                any_of_scopes=False,
                rate_bucket="governance_admin",
                supports_idempotency=True,
                handler=_id_revoke,
            ),
            RouteSpec(
                method="DELETE",
                pattern=re.compile(r"^/v1/candidates/(?P<candidate_id>[^/]+)/pii$"),
                required_scopes=(RbacScope.GOVERNANCE_ADMIN,),
                any_of_scopes=False,
                rate_bucket="governance_admin",
                supports_idempotency=False,
                handler=_id_erase,
            ),
            RouteSpec(
                method="GET",
                pattern=re.compile(r"^/health/live$"),
                required_scopes=(),
                any_of_scopes=False,
                rate_bucket="ops_health",
                supports_idempotency=False,
                handler=_ops_live,
                requires_auth=False,
            ),
            RouteSpec(
                method="GET",
                pattern=re.compile(r"^/health/ready$"),
                required_scopes=(),
                any_of_scopes=False,
                rate_bucket="ops_health",
                supports_idempotency=False,
                handler=_ops_ready,
                requires_auth=False,
            ),
            RouteSpec(
                method="GET",
                pattern=re.compile(r"^/v1/ops/metrics$"),
                required_scopes=(RbacScope.GOVERNANCE_ADMIN,),
                any_of_scopes=False,
                rate_bucket="governance_admin",
                supports_idempotency=False,
                handler=_ops_metrics,
            ),
        )

    def _resolve_route(
        self, method: str, path: str
    ) -> tuple[RouteSpec | None, dict[str, str], bool]:
        upper_method = method.upper()
        path_matched_other_method = False
        for route in self._routes:
            match = route.pattern.fullmatch(path)
            if match is None:
                continue
            if route.method == upper_method:
                return route, match.groupdict(), False
            path_matched_other_method = True
        return None, {}, path_matched_other_method

    async def handle(self, request: HttpRequest) -> HttpResponse:
        """Execute auth, cross-tenant, RBAC, rate-limit, idempotency, and error mapping."""
        request_id = request.header("X-Request-ID") or str(generate_uuid7())
        trace_id = request.header("X-Trace-ID") or str(generate_uuid7())
        norm_path = _normalize_v1_path(request.path)

        route, path_params, wrong_method = self._resolve_route(request.method, norm_path)
        if route is None:
            status = 405 if wrong_method else 404
            err_code = "METHOD_NOT_ALLOWED" if wrong_method else "ROUTE_NOT_FOUND"
            title = "Method Not Allowed" if wrong_method else "Not Found"
            detail = (
                f"HTTP method {request.method.upper()} not allowed on {norm_path}."
                if wrong_method
                else f"No route registered matching {request.method.upper()} {norm_path}."
            )
            problem = self._translator._build(
                status=status,
                title=title,
                detail=detail,
                error_code=err_code,
                category="routing",
                instance=norm_path,
                trace_id=trace_id,
            )
            return HttpResponse(
                status_code=status,
                body=problem.to_dict(),
                headers={"X-Request-ID": request_id, "X-Trace-ID": trace_id},
            )

        base_headers: dict[str, str] = {"X-Request-ID": request_id, "X-Trace-ID": trace_id}
        principal: PrincipalContext | None = None
        try:
            if not route.requires_auth:
                sys_tenant = _PUBLIC_HEALTH_TENANT
                public_principal = PrincipalContext(
                    tenant_id=sys_tenant,
                    actor_id=UUID(int=0),
                    scopes=frozenset(),
                    token_jti="public-health",
                    expires_at=utc_now(),
                )
                public_meta = ResponseMeta(
                    request_id=request_id,
                    trace_id=trace_id,
                    tenant_id=sys_tenant,
                )
                pub_res = await route.handler(request, path_params, public_principal, public_meta)
                return pub_res.with_headers(base_headers)

            principal = self._auth.authenticate(request.header("Authorization"))
            if request.body is not None and "tenant_id" in request.body:
                self._auth.assert_tenant_match(
                    principal,
                    request.body["tenant_id"],  # type: ignore[arg-type]
                )
            if "tenant_id" in request.query_params:
                self._auth.assert_tenant_match(principal, request.query_params["tenant_id"])

            self._auth.require_scopes(
                principal, route.required_scopes, any_of=route.any_of_scopes
            )
            rl_headers = self._rate_limiter.check_and_consume(
                tenant_id=principal.tenant_id,
                bucket=route.rate_bucket,
            )
            base_headers.update(rl_headers.to_header_dict())

            meta = ResponseMeta(
                request_id=request_id,
                trace_id=trace_id,
                tenant_id=principal.tenant_id,
            )

            async def _invoke_controller() -> HttpResponse:
                return await route.handler(request, path_params, principal, meta)

            if route.supports_idempotency:
                idem_key = request.header("Idempotency-Key")
                response = await self._idempotency.execute_idempotent(
                    tenant_id=principal.tenant_id,
                    operation=f"{route.method}:{route.pattern.pattern}",
                    idempotency_key=idem_key,
                    method=route.method,
                    path=norm_path,
                    body=request.body,
                    handler=_invoke_controller,
                    require_key=False,
                )
            else:
                response = await _invoke_controller()

            return response.with_headers(base_headers)
        except Exception as exc:
            problem = self._translator.translate(exc, instance=norm_path, trace_id=trace_id)
            err_headers = dict(base_headers)
            if isinstance(exc, AuthenticationError):
                err_headers["WWW-Authenticate"] = "Bearer"
            if problem.retry_after_seconds is not None:
                if principal is not None:
                    err_headers.update(
                        self._rate_limiter.current_headers_for_error(
                            tenant_id=principal.tenant_id,
                            bucket=route.rate_bucket,
                            retry_after=problem.retry_after_seconds,
                        )
                    )
                else:
                    err_headers["Retry-After"] = str(problem.retry_after_seconds)
            return HttpResponse(
                status_code=problem.status,
                body=problem.to_dict(),
                headers=err_headers,
            )
