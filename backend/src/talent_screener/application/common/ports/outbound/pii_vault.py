"""Outbound Application Port for cryptographic PII vaulting and GDPR crypto-shredding."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.common.dtos import (
    CryptoShredReceiptDTO,
    PiiTokenizationOutcomeDTO,
)
from talent_screener.domain.shared.value_objects import TenantId


@runtime_checkable
class PiiVaultPort(Protocol):
    """Port isolating candidate PII behind KMS envelope encryption and crypto-shredding."""

    async def tokenize_and_vault(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
        raw_text: str,
    ) -> PiiTokenizationOutcomeDTO: ...

    async def detokenize(
        self,
        *,
        tenant_id: TenantId,
        vault_reference_id: UUID,
    ) -> Mapping[str, str]: ...

    async def crypto_shred(
        self,
        *,
        tenant_id: TenantId,
        candidate_id: UUID,
        reason: str,
    ) -> CryptoShredReceiptDTO: ...
