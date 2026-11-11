"""Content-addressed, tenant-scoped Object Storage infrastructure adapter."""

from __future__ import annotations

import hashlib
import hmac
import os
import posixpath
from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

from talent_screener.application.common.exceptions import (
    ExternalAdapterFailureError,
    ResourceNotFoundError,
    UnauthorizedTenantAccessError,
)
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import SHA256Hash, TenantId


def _require_tenant(tenant_id: object) -> TenantId:
    if not isinstance(tenant_id, TenantId) or not isinstance(tenant_id.value, UUID):
        raise UnauthorizedTenantAccessError(
            "Valid TenantId is required for object storage operations.",
            code="MISSING_TENANT_CONTEXT",
        )
    return tenant_id


def _sanitize_and_validate_key(tenant_id: TenantId, object_key: str) -> str:
    if not isinstance(object_key, str) or not object_key.strip():
        raise ExternalAdapterFailureError(
            "Object key must be a non-empty string.",
            code="INVALID_OBJECT_KEY",
            retryable=False,
        )
    if "\x00" in object_key or "\\" in object_key or object_key.startswith("/"):
        raise UnauthorizedTenantAccessError(
            "Invalid object key format: path traversal or absolute path disallowed.",
            code="INVALID_OBJECT_KEY",
        )
    raw_segments = object_key.split("/")
    if any(seg in {"", ".", ".."} for seg in raw_segments):
        raise UnauthorizedTenantAccessError(
            "Invalid object key segments: directory traversal disallowed.",
            code="INVALID_OBJECT_KEY",
        )
    normalized = posixpath.normpath(object_key)
    segments = normalized.split("/")
    # Verify embedded tenant UUID segment when key carries tenant prefix or quarantine prefix.
    candidate_tenant_seg: str | None = None
    if segments[0] == "quarantine" and len(segments) >= 2:
        candidate_tenant_seg = segments[1]
    elif len(segments) >= 2:
        candidate_tenant_seg = segments[0]

    if candidate_tenant_seg is not None:
        try:
            embedded_uuid = UUID(candidate_tenant_seg)
        except ValueError:
            embedded_uuid = None
        if embedded_uuid is not None and embedded_uuid != tenant_id.value:
            raise UnauthorizedTenantAccessError(
                f"Cross-tenant object access denied for key {object_key!r}.",
                code="CROSS_TENANT_STORAGE_ACCESS",
            )
    return normalized


@dataclass(frozen=True, slots=True)
class StoredBlobRecord:
    """Immutable binary payload and content-addressed integrity metadata."""

    tenant_id: UUID
    object_key: str
    payload: bytes
    content_type: str
    sha256: SHA256Hash


class TenantScopedObjectStorageAdapter:
    """ObjectStoragePort with tenant prefix isolation and SHA-256 verification."""

    def __init__(
        self,
        *,
        base_url: str = "https://storage.local/v1/blobs",
        signing_secret: bytes | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._signing_secret = (
            signing_secret if signing_secret is not None else os.urandom(32)
        )
        self._objects: dict[tuple[UUID, str], StoredBlobRecord] = {}

    @staticmethod
    def build_content_addressed_key(
        *,
        tenant_id: TenantId,
        batch_id: UUID,
        content_sha256: SHA256Hash,
    ) -> str:
        """Build canonical tenant-prefixed content-addressed storage key."""
        _require_tenant(tenant_id)
        return f"{tenant_id.value}/{batch_id}/{content_sha256.value}"

    def _canonical_scoped_key(self, tenant_id: TenantId, object_key: str) -> str:
        clean = _sanitize_and_validate_key(tenant_id, object_key)
        prefix = f"{tenant_id.value}/"
        quarantine_prefix = f"quarantine/{tenant_id.value}/"
        if clean.startswith((prefix, quarantine_prefix)):
            return clean
        return f"{tenant_id.value}/{clean}"

    async def generate_presigned_upload_url(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
        content_sha256: SHA256Hash,
        content_type: str,
        expires_in_seconds: int = 900,
    ) -> str:
        """Issue an HMAC-signed tenant-scoped presigned upload URI bound to SHA-256."""
        _require_tenant(tenant_id)
        if expires_in_seconds <= 0:
            raise ExternalAdapterFailureError(
                "Presigned URL expiration must be positive.",
                code="INVALID_EXPIRATION",
                retryable=False,
            )
        scoped_key = self._canonical_scoped_key(tenant_id, object_key)
        expires_epoch = int(utc_now().timestamp()) + expires_in_seconds
        canonical_to_sign = (
            f"{tenant_id.value}:{scoped_key}:{content_sha256.value}:"
            f"{content_type}:{expires_epoch}"
        ).encode()
        signature = hmac.new(self._signing_secret, canonical_to_sign, hashlib.sha256).hexdigest()
        query = urlencode(
            {
                "sha256": content_sha256.value,
                "content_type": content_type,
                "expires": str(expires_epoch),
                "sig": signature,
            }
        )
        return f"{self._base_url}/{scoped_key}?{query}"

    async def put_bytes(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
        payload: bytes,
        content_type: str,
    ) -> SHA256Hash:
        """Persist bytes under tenant scope and return computed SHA-256 digest."""
        _require_tenant(tenant_id)
        scoped_key = self._canonical_scoped_key(tenant_id, object_key)
        digest = SHA256Hash.from_bytes(payload)
        self._objects[(tenant_id.value, scoped_key)] = StoredBlobRecord(
            tenant_id=tenant_id.value,
            object_key=scoped_key,
            payload=bytes(payload),
            content_type=content_type,
            sha256=digest,
        )
        return digest

    async def put_verified_bytes(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
        payload: bytes,
        content_type: str,
        expected_sha256: SHA256Hash,
    ) -> SHA256Hash:
        """Persist bytes after strictly verifying content matches expected SHA-256."""
        actual = SHA256Hash.from_bytes(payload)
        if actual != expected_sha256:
            raise ExternalAdapterFailureError(
                f"Uploaded payload SHA-256 mismatch for key {object_key!r}.",
                code="CHECKSUM_MISMATCH",
                retryable=False,
            )
        return await self.put_bytes(
            tenant_id=tenant_id,
            object_key=object_key,
            payload=payload,
            content_type=content_type,
        )

    async def get_bytes(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
    ) -> bytes:
        """Fetch bytes scoped to tenant_id and verify stored SHA-256 integrity."""
        _require_tenant(tenant_id)
        scoped_key = self._canonical_scoped_key(tenant_id, object_key)
        rec = self._objects.get((tenant_id.value, scoped_key))
        if rec is None:
            raise ResourceNotFoundError(
                f"Object {object_key!r} not found for tenant {tenant_id}.",
                code="OBJECT_NOT_FOUND",
            )
        actual_sha256 = SHA256Hash.from_bytes(rec.payload)
        if actual_sha256 != rec.sha256:
            raise ExternalAdapterFailureError(
                f"Stored object integrity check failed for key {object_key!r}.",
                code="STORAGE_CHECKSUM_CORRUPTION",
                retryable=False,
            )
        return rec.payload

    async def delete_object(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
    ) -> None:
        """Delete tenant-scoped object cleanly (idempotent if already absent)."""
        _require_tenant(tenant_id)
        scoped_key = self._canonical_scoped_key(tenant_id, object_key)
        self._objects.pop((tenant_id.value, scoped_key), None)
