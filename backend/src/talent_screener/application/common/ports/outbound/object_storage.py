"""Outbound Application Port for content-addressed object blob storage."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.domain.shared.value_objects import SHA256Hash, TenantId


@runtime_checkable
class ObjectStoragePort(Protocol):
    """Port abstracting S3/GCS encrypted binary blob storage and presigned uploads."""

    async def generate_presigned_upload_url(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
        content_sha256: SHA256Hash,
        content_type: str,
        expires_in_seconds: int = 900,
    ) -> str: ...

    async def put_bytes(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
        payload: bytes,
        content_type: str,
    ) -> SHA256Hash: ...

    async def get_bytes(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
    ) -> bytes: ...

    async def delete_object(
        self,
        *,
        tenant_id: TenantId,
        object_key: str,
    ) -> None: ...
