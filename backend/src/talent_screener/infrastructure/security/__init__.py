"""Security, Virus Quarantine, PII Vault & Defense-in-Depth Infrastructure Adapters."""

from talent_screener.infrastructure.security.defense_in_depth import (
    AdversarialStrikeTracker,
    CanaryQuarantineRecord,
    CanaryTokenLeakVerifier,
    DefenseInDepthFirewall,
    DefenseTurnDecision,
    DualLlmQuarantineExtractor,
    NonceDelimiterEnvelope,
    OutboundCanaryCheckResult,
    QuarantinedExtractionResult,
    QuarantineReaderProtocol,
    SandboxedEnvelope,
    StrikeAction,
    StrikePolicyOutcome,
    TechnicalClaimRecord,
)
from talent_screener.infrastructure.security.pii_vault import (
    CryptographicPiiVaultAdapter,
    EnvelopeCipherProtocol,
    StdlibHmacCtrEnvelopeCipher,
    VaultRecordAuditSnapshot,
)
from talent_screener.infrastructure.security.prompt_guardrail import (
    AdversarialPromptGuardrailAdapter,
    AdversarialThreatCategory,
    GuardrailRiskAssessment,
)
from talent_screener.infrastructure.security.virus_scanner import (
    SignatureAndHeuristicVirusScannerAdapter,
)

__all__ = [
    "AdversarialPromptGuardrailAdapter",
    "AdversarialStrikeTracker",
    "AdversarialThreatCategory",
    "CanaryQuarantineRecord",
    "CanaryTokenLeakVerifier",
    "CryptographicPiiVaultAdapter",
    "DefenseInDepthFirewall",
    "DefenseTurnDecision",
    "DualLlmQuarantineExtractor",
    "EnvelopeCipherProtocol",
    "GuardrailRiskAssessment",
    "NonceDelimiterEnvelope",
    "OutboundCanaryCheckResult",
    "QuarantineReaderProtocol",
    "QuarantinedExtractionResult",
    "SandboxedEnvelope",
    "SignatureAndHeuristicVirusScannerAdapter",
    "StdlibHmacCtrEnvelopeCipher",
    "StrikeAction",
    "StrikePolicyOutcome",
    "TechnicalClaimRecord",
    "VaultRecordAuditSnapshot",
]
