"""Outbound Application Port for malware and container magic-byte scanning."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.common.dtos import VirusScanOutcomeDTO
from talent_screener.domain.shared.value_objects import SHA256Hash, TenantId


@runtime_checkable
class VirusScannerPort(Protocol):
    """Port verifying document binary streams against malware signatures and SHA-256."""

    async def scan_bytes(
        self,
        *,
        tenant_id: TenantId,
        filename: str,
        payload: bytes,
        expected_sha256: SHA256Hash,
    ) -> VirusScanOutcomeDTO: ...
