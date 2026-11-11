"""Inbound Driving Ports for Identity & Tenant Governance Use Cases."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.identity_governance.dtos import (
    CandidateErasureReceiptDTO,
    ConsentRecordResponseDTO,
    ExecuteCandidateErasureRequestDTO,
    ProvisionTenantAccountRequestDTO,
    ProvisionTenantAccountResponseDTO,
    RecordCandidateConsentRequestDTO,
    RevokeCandidateConsentRequestDTO,
)


@runtime_checkable
class ProvisionTenantAccountPort(Protocol):
    """Inbound port for provisioning a TenantAccount."""

    async def execute(
        self, request: ProvisionTenantAccountRequestDTO
    ) -> ProvisionTenantAccountResponseDTO: ...


@runtime_checkable
class RecordCandidateConsentPort(Protocol):
    """Inbound port for recording candidate consent."""

    async def execute(
        self, request: RecordCandidateConsentRequestDTO
    ) -> ConsentRecordResponseDTO: ...


@runtime_checkable
class RevokeCandidateConsentPort(Protocol):
    """Inbound port for withdrawing candidate consent."""

    async def execute(
        self, request: RevokeCandidateConsentRequestDTO
    ) -> ConsentRecordResponseDTO: ...


@runtime_checkable
class ExecuteCandidateErasurePort(Protocol):
    """Inbound port for executing GDPR cryptographic erasure."""

    async def execute(
        self, request: ExecuteCandidateErasureRequestDTO
    ) -> CandidateErasureReceiptDTO: ...
