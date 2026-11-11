"""Identity & Tenant Governance inbound port exports."""

from talent_screener.application.identity_governance.ports.inbound.identity_ports import (
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
