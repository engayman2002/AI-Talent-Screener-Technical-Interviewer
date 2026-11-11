"""HTTP Idempotency-Key + SHA-256 Request Fingerprinting Middleware."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping

from talent_screener.application.common.dtos import IdempotencyStatus
from talent_screener.application.common.exceptions import IdempotencyConflictError
from talent_screener.application.common.ports.outbound import IdempotencyStorePort
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import SHA256Hash, TenantId
from talent_screener.presentation.exceptions import RequestValidationError
from talent_screener.presentation.schemas.envelopes import HttpResponse


class IdempotencyMiddleware:
    """Enforces Idempotency-Key + SHA-256 request fingerprinting via IdempotencyStorePort."""

    def __init__(self, *, store: IdempotencyStorePort) -> None:
        self._store = store

    @staticmethod
    def fingerprint_request(
        *,
        method: str,
        path: str,
        body: Mapping[str, object] | None,
    ) -> str:
        """Compute deterministic SHA-256 digest of method, path, and sorted JSON body."""
        canonical_body = json.dumps(body or {}, sort_keys=True, separators=(",", ":"), default=str)
        raw = f"{method.upper()}:{path}:{canonical_body}"
        return SHA256Hash.from_text(raw).value

    async def execute_idempotent(
        self,
        *,
        tenant_id: TenantId,
        operation: str,
        idempotency_key: str | None,
        method: str,
        path: str,
        body: Mapping[str, object] | None,
        handler: Callable[[], Awaitable[HttpResponse]],
        require_key: bool = False,
    ) -> HttpResponse:
        """Replay cached HTTP responses or atomically execute and cache mutating calls."""
        key = idempotency_key.strip() if idempotency_key else ""
        if not key:
            if require_key:
                raise RequestValidationError(
                    "Idempotency-Key header is required for this operation.",
                    code="MISSING_IDEMPOTENCY_KEY",
                )
            return await handler()

        payload_hash = self.fingerprint_request(method=method, path=path, body=body)
        existing = await self._store.check_and_reserve(
            tenant_id=tenant_id,
            operation=operation,
            idempotency_key=key,
            payload_hash=payload_hash,
        )
        if existing is not None:
            if existing.status is IdempotencyStatus.IN_PROGRESS:
                raise IdempotencyConflictError(
                    "Request with this Idempotency-Key is currently in progress.",
                    code="IDEMPOTENCY_IN_PROGRESS",
                )
            if existing.serialized_result is not None:
                raw_status = existing.serialized_result.get("status_code", 200)
                cached_status = raw_status if isinstance(raw_status, int) else 200
                cached_body = existing.serialized_result.get("body")
                body_map = cached_body if isinstance(cached_body, Mapping) else {}
                return HttpResponse(
                    status_code=cached_status,
                    body=body_map,
                ).with_headers({"X-Idempotency-Replayed": "true"})

        try:
            response = await handler()
            await self._store.mark_completed(
                tenant_id=tenant_id,
                operation=operation,
                idempotency_key=key,
                resource_id=generate_uuid7(),
                serialized_result={
                    "status_code": response.status_code,
                    "body": dict(response.body) if response.body is not None else {},
                },
            )
            return response.with_headers({"X-Idempotency-Replayed": "false"})
        except Exception:
            await self._store.release_reservation(
                tenant_id=tenant_id,
                operation=operation,
                idempotency_key=key,
            )
            raise
