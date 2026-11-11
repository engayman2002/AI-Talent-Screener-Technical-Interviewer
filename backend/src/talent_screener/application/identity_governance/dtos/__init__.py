"""Identity & Tenant Governance DTO exports."""

from talent_screener.application.identity_governance.dtos.contracts import (
    CandidateErasureReceiptDTO,
    ConsentRecordResponseDTO,
    ExecuteCandidateErasureRequestDTO,
    ProvisionTenantAccountRequestDTO,
    ProvisionTenantAccountResponseDTO,
    RecordCandidateConsentRequestDTO,
    RevokeCandidateConsentRequestDTO,
)

__all__ = [
    "CandidateErasureReceiptDTO",
    "ConsentRecordResponseDTO",
    "ExecuteCandidateErasureRequestDTO",
    "ProvisionTenantAccountRequestDTO",
    "ProvisionTenantAccountResponseDTO",
    "RecordCandidateConsentRequestDTO",
    "RevokeCandidateConsentRequestDTO",
]
