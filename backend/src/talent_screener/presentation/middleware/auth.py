"""HMAC-SHA256 Bearer JWT Authentication, Cross-Tenant Isolation & RBAC Middleware."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from talent_screener.application.common.exceptions import UnauthorizedTenantAccessError
from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.presentation.exceptions import (
    AuthenticationError,
    MissingRbacScopeError,
)


class RbacScope:
    """Canonical RBAC permission scopes enforced by Presentation endpoints."""

    RECRUITMENT_WRITE = "recruitment:write"
    RECRUITMENT_READ = "recruitment:read"
    SCREENING_WRITE = "screening:write"
    SCREENING_READ = "screening:read"
    INTERVIEW_CANDIDATE = "interview:candidate"
    INTERVIEW_MANAGE = "interview:manage"
    EVALUATION_READ = "evaluation:read"
    EVALUATION_OVERRIDE = "evaluation:override"
    GOVERNANCE_ADMIN = "governance:admin"


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    """Verified immutable authentication & authorization claims bound per request."""

    tenant_id: TenantId
    actor_id: UUID
    scopes: frozenset[str]
    token_jti: str
    expires_at: datetime


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * ((4 - len(segment) % 4) % 4)
    return base64.urlsafe_b64decode(segment + padding)


class BearerTokenAuthenticator:
    """Validates HMAC-SHA256 compact JWT tokens and enforces tenant/RBAC isolation."""

    def __init__(
        self,
        *,
        signing_key: bytes | None = None,
        clock_fn: Callable[[], float] | None = None,
    ) -> None:
        if signing_key is not None and len(signing_key) < 16:
            raise ValueError("signing_key must be at least 16 bytes.")
        # Always generate fresh CSPRNG key when omitted; never hardcode static secrets
        self._signing_key: bytes = (
            signing_key if signing_key is not None else os.urandom(32)
        )
        self._clock = clock_fn if clock_fn is not None else time.time

    def issue_token(
        self,
        *,
        tenant_id: TenantId,
        actor_id: UUID,
        scopes: Sequence[str],
        ttl_seconds: int = 3600,
        token_jti: str | None = None,
    ) -> str:
        """Issue a compact HS256 JWT token carrying tenant_id, actor_id, scopes, and jti."""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive.")
        now = int(self._clock())
        header_b64 = _b64url_encode(
            json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8")
        )
        payload = {
            "sub": str(actor_id),
            "tenant_id": str(tenant_id.value),
            "scopes": list(dict.fromkeys(scopes)),
            "jti": token_jti or str(generate_uuid7()),
            "iat": now,
            "exp": now + ttl_seconds,
        }
        payload_b64 = _b64url_encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        signature = hmac.new(self._signing_key, signing_input, hashlib.sha256).digest()
        return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"

    def authenticate(self, authorization_header: str | None) -> PrincipalContext:
        """Verify Bearer token header and extract PrincipalContext."""
        if not authorization_header:
            raise AuthenticationError(
                "Missing Authorization Bearer header.",
                code="MISSING_AUTH_HEADER",
            )
        parts = authorization_header.split(" ", maxsplit=1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise AuthenticationError(
                "Authorization header must use Bearer token scheme.",
                code="INVALID_AUTH_SCHEME",
            )
        return self.verify_raw_token(parts[1].strip())

    def verify_raw_token(self, token: str) -> PrincipalContext:
        """Verify raw HS256 JWT string against signature, algorithm, expiration, and claims."""
        segments = token.split(".")
        if len(segments) != 3:
            raise AuthenticationError("Malformed JWT token format.", code="MALFORMED_JWT")

        header_b64, payload_b64, sig_b64 = segments
        try:
            header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
            payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
            provided_sig = _b64url_decode(sig_b64)
        except Exception as exc:
            raise AuthenticationError(
                "Invalid JWT Base64/JSON encoding.",
                code="INVALID_JWT_ENCODING",
            ) from exc

        if not isinstance(header, dict) or header.get("alg") != "HS256":
            raise AuthenticationError(
                "Unsupported JWT signing algorithm; HS256 required.",
                code="UNSUPPORTED_JWT_ALG",
            )

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected_sig = hmac.new(self._signing_key, signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(provided_sig, expected_sig):
            raise AuthenticationError(
                "JWT cryptographic signature verification failed.",
                code="INVALID_JWT_SIGNATURE",
            )

        if not isinstance(payload, dict):
            raise AuthenticationError(
                "JWT claims must be a JSON object.", code="INVALID_JWT_CLAIMS"
            )

        exp_raw = payload.get("exp")
        if not isinstance(exp_raw, int | float) or isinstance(exp_raw, bool):
            raise AuthenticationError("JWT missing valid numeric 'exp' claim.", code="MISSING_EXP")
        if float(exp_raw) <= self._clock():
            raise AuthenticationError("JWT token has expired.", code="TOKEN_EXPIRED")

        try:
            actor_id = UUID(str(payload.get("sub", "")))
            tenant_id = TenantId.from_str(str(payload.get("tenant_id", "")))
        except Exception as exc:
            raise AuthenticationError(
                "JWT missing valid 'sub' or 'tenant_id' UUID claims.",
                code="INVALID_IDENTITY_CLAIMS",
            ) from exc

        jti_raw = str(payload.get("jti", "")).strip()
        if not jti_raw:
            raise AuthenticationError("JWT missing 'jti' token identifier.", code="MISSING_JTI")

        raw_scopes = payload.get("scopes", ())
        if not isinstance(raw_scopes, list) or not all(isinstance(s, str) for s in raw_scopes):
            raise AuthenticationError(
                "JWT 'scopes' must be a list of strings.", code="INVALID_SCOPES"
            )

        return PrincipalContext(
            tenant_id=tenant_id,
            actor_id=actor_id,
            scopes=frozenset(raw_scopes),
            token_jti=jti_raw,
            expires_at=datetime.fromtimestamp(float(exp_raw), tz=UTC),
        )

    @staticmethod
    def assert_tenant_match(
        principal: PrincipalContext,
        requested_tenant_id: TenantId | UUID | str | None,
    ) -> None:
        """Fail closed with 403 Forbidden if requested tenant conflicts with principal claim."""
        if requested_tenant_id is None:
            return
        if isinstance(requested_tenant_id, TenantId):
            candidate_uuid = requested_tenant_id.value
        elif isinstance(requested_tenant_id, UUID):
            candidate_uuid = requested_tenant_id
        else:
            try:
                candidate_uuid = UUID(str(requested_tenant_id))
            except ValueError:
                return
        if candidate_uuid != principal.tenant_id.value:
            raise UnauthorizedTenantAccessError(
                "Cross-tenant resource access forbidden for authenticated principal.",
                code="CROSS_TENANT_ACCESS_DENIED",
                metadata={
                    "principal_tenant_id": str(principal.tenant_id.value),
                    "requested_tenant_id": str(candidate_uuid),
                },
            )

    @staticmethod
    def require_scopes(
        principal: PrincipalContext,
        required_scopes: Sequence[str],
        *,
        any_of: bool = False,
    ) -> None:
        """Enforce RBAC scope presence on PrincipalContext or raise MissingRbacScopeError."""
        if not required_scopes:
            return
        if any_of:
            if any(scope in principal.scopes for scope in required_scopes):
                return
            raise MissingRbacScopeError(
                f"Requires one of RBAC scopes {tuple(required_scopes)}.",
                missing_scopes=required_scopes,
            )
        missing = [scope for scope in required_scopes if scope not in principal.scopes]
        if missing:
            raise MissingRbacScopeError(
                f"Missing required RBAC scope(s): {missing}.",
                missing_scopes=missing,
            )
