"""Identity & Tenant Governance port exports."""

from talent_screener.application.identity_governance.ports.inbound import (
    ExecuteCandidateErasurePort,
    ProvisionTenantAccountPort,
    RecordCandidateConsentPort,
    RevokeCandidateConsentPort,
)

__all__ = [
    "ExecuteCandidateErasurePort",
    "ProvisionTenantAccountPort",
    "RecordCandidateConsentPort",
    "RevokeCandidateConsentPort",
]
