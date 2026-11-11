"""Stage 3 Cryptographic PII Vault & GDPR Crypto-Shredding Adapter (ADR-008)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.common.dtos import (
    CryptoShredReceiptDTO,
    PiiTokenizationOutcomeDTO,
)
from talent_screener.application.common.exceptions import (
    ExternalAdapterFailureError,
    ResourceConflictError,
    ResourceNotFoundError,
    UnauthorizedTenantAccessError,
)
from talent_screener.domain.contexts.document_intelligence.value_objects import PiiRedactionMap
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import TenantId

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(
    r"(?:https?://[^\s<>()\]\"']+|(?:www\.)?(?:linkedin\.com/in|github\.com)/[A-Za-z0-9_./-]+)"
)
_NATIONAL_ID_RE = re.compile(
    r"(?:\b\d{3}-\d{2}-\d{4}\b"
    r"|(?:ssn|national[\s_-]*id|passport(?:\s*no)?)\s*[:#]\s*[A-Z0-9-]{5,20})",
    re.I,
)
_PHONE_RE = re.compile(
    r"(?:\+\d{1,3}[-.\s]?)?(?:\(\d{3}\)|\b\d{3})[-.\s]\d{3}[-.\s]\d{4}\b"
    r"|\+\d{1,3}[-.\s]\d{2,4}[-.\s]\d{3,4}[-.\s]\d{3,4}\b"
)
_LABELED_NAME_RE = re.compile(
    r"(?im)^(?:candidate(?:\s+name)?|full\s+name|name)\s*:\s*([^\n\r]{2,80})$"
)
_LABELED_ADDRESS_RE = re.compile(r"(?im)^(?:address|location|residence)\s*:\s*([^\n\r]{5,120})$")

_TAG_BY_CATEGORY: dict[str, str] = {
    "EMAIL": "EMAIL",
    "PHONE": "PHONE",
    "URL": "URL",
    "NATIONAL_ID": "NATIONAL_ID",
    "PERSON_NAME": "NAME",
    "ADDRESS": "ADDRESS",
}


@runtime_checkable
class EnvelopeCipherProtocol(Protocol):
    """Pluggable authenticated envelope cipher driver protocol (KMS / AES-GCM compatible)."""

    def generate_dek(self) -> bytes: ...

    def encrypt_with_aad(self, key: bytes, plaintext: bytes, *, aad: bytes) -> bytes: ...

    def decrypt_with_aad(self, key: bytes, ciphertext_bundle: bytes, *, aad: bytes) -> bytes: ...


class StdlibHmacCtrEnvelopeCipher:
    """Pure-stdlib Encrypt-then-MAC (SHA-256 CTR + HMAC-SHA256) authenticated cipher."""

    _VERSION = b"\x01"
    _NONCE_LEN = 16
    _TAG_LEN = 32

    def generate_dek(self) -> bytes:
        return os.urandom(32)

    @staticmethod
    def _derive_subkeys(master_key: bytes) -> tuple[bytes, bytes]:
        enc_key = hmac.new(master_key, b"talent_screener:aead:enc", hashlib.sha256).digest()
        mac_key = hmac.new(master_key, b"talent_screener:aead:mac", hashlib.sha256).digest()
        return enc_key, mac_key

    @staticmethod
    def _keystream_xor(enc_key: bytes, nonce: bytes, data: bytes) -> bytes:
        out = bytearray(len(data))
        offset = 0
        counter = 0
        while offset < len(data):
            block = hmac.new(
                enc_key,
                nonce + counter.to_bytes(8, "big"),
                hashlib.sha256,
            ).digest()
            take = min(len(block), len(data) - offset)
            for idx in range(take):
                out[offset + idx] = data[offset + idx] ^ block[idx]
            offset += take
            counter += 1
        return bytes(out)

    def encrypt_with_aad(self, key: bytes, plaintext: bytes, *, aad: bytes) -> bytes:
        if len(key) != 32:
            raise ExternalAdapterFailureError(
                "Envelope cipher key must be 32 bytes.",
                code="INVALID_CIPHER_KEY",
                retryable=False,
            )
        enc_key, mac_key = self._derive_subkeys(key)
        nonce = os.urandom(self._NONCE_LEN)
        ciphertext = self._keystream_xor(enc_key, nonce, plaintext)
        tag = hmac.new(
            mac_key,
            self._VERSION + nonce + aad + ciphertext,
            hashlib.sha256,
        ).digest()
        return self._VERSION + nonce + tag + ciphertext

    def decrypt_with_aad(self, key: bytes, ciphertext_bundle: bytes, *, aad: bytes) -> bytes:
        header_len = 1 + self._NONCE_LEN + self._TAG_LEN
        if len(key) != 32 or len(ciphertext_bundle) < header_len:
            raise ExternalAdapterFailureError(
                "Malformed encrypted PII vault bundle.",
                code="VAULT_INTEGRITY_ERROR",
                retryable=False,
            )
        if ciphertext_bundle[:1] != self._VERSION:
            raise ExternalAdapterFailureError(
                "Unsupported encrypted vault bundle version.",
                code="VAULT_INTEGRITY_ERROR",
                retryable=False,
            )
        nonce = ciphertext_bundle[1 : 1 + self._NONCE_LEN]
        tag = ciphertext_bundle[1 + self._NONCE_LEN : header_len]
        ciphertext = ciphertext_bundle[header_len:]
        enc_key, mac_key = self._derive_subkeys(key)
        expected_tag = hmac.new(
            mac_key,
            self._VERSION + nonce + aad + ciphertext,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(tag, expected_tag):
            raise ExternalAdapterFailureError(
                "Vault ciphertext authentication tag verification failed.",
                code="VAULT_INTEGRITY_ERROR",
                retryable=False,
            )
        return self._keystream_xor(enc_key, nonce, ciphertext)


@dataclass(slots=True)
class _EncryptedVaultRecord:
    vault_reference_id: UUID
    tenant_id: UUID
    candidate_id: UUID
    key_id: UUID
    wrapped_dek: bytes
    encrypted_payload: bytes
    token_categories: tuple[tuple[str, str], ...]
    blind_email_hmac_index: str | None
    is_shredded: bool = False
    shredded_at: datetime | None = None
    erasure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class VaultRecordAuditSnapshot:
    """Non-sensitive inspection view verifying key separation and shred state."""

    vault_reference_id: UUID
    tenant_id: UUID
    candidate_id: UUID
    key_id: UUID
    is_shredded: bool
    has_ciphertext: bool
    has_wrapped_dek: bool
    blind_email_hmac_index: str | None
    shredded_at: datetime | None


def _require_tenant(tenant_id: object) -> TenantId:
    if not isinstance(tenant_id, TenantId) or not isinstance(tenant_id.value, UUID):
        raise UnauthorizedTenantAccessError(
            "Valid TenantId is required for PII Vault access.",
            code="MISSING_TENANT_CONTEXT",
        )
    return tenant_id


@dataclass(frozen=True, slots=True)
class _DetectedSpan:
    start: int
    end: int
    category: str
    raw_value: str


def _detect_pii_spans(text: str) -> list[_DetectedSpan]:
    candidates: list[_DetectedSpan] = []

    for match in _EMAIL_RE.finditer(text):
        candidates.append(_DetectedSpan(match.start(), match.end(), "EMAIL", match.group(0)))
    for match in _URL_RE.finditer(text):
        candidates.append(_DetectedSpan(match.start(), match.end(), "URL", match.group(0)))
    for match in _NATIONAL_ID_RE.finditer(text):
        val = match.group(0)
        if ":" in val or "#" in val:
            sep_idx = max(val.find(":"), val.find("#")) + 1
            raw_part = val[sep_idx:].strip()
            offset = match.start() + val.find(raw_part, sep_idx)
            candidates.append(
                _DetectedSpan(offset, offset + len(raw_part), "NATIONAL_ID", raw_part)
            )
        else:
            candidates.append(_DetectedSpan(match.start(), match.end(), "NATIONAL_ID", val))
    for match in _PHONE_RE.finditer(text):
        candidates.append(_DetectedSpan(match.start(), match.end(), "PHONE", match.group(0)))
    for match in _LABELED_NAME_RE.finditer(text):
        val = match.group(1).strip()
        if val and not val.startswith("[PII_"):
            candidates.append(
                _DetectedSpan(
                    match.start(1),
                    match.start(1) + len(match.group(1)),
                    "PERSON_NAME",
                    val,
                )
            )
    for match in _LABELED_ADDRESS_RE.finditer(text):
        val = match.group(1).strip()
        if val and not val.startswith("[PII_"):
            candidates.append(
                _DetectedSpan(
                    match.start(1), match.start(1) + len(match.group(1)), "ADDRESS", val
                )
            )

    priority = {
        "EMAIL": 0,
        "URL": 1,
        "NATIONAL_ID": 2,
        "PHONE": 3,
        "PERSON_NAME": 4,
        "ADDRESS": 5,
    }
    candidates.sort(key=lambda s: (s.start, -(s.end - s.start), priority[s.category]))

    non_overlapping: list[_DetectedSpan] = []
    cursor = 0
    for span in candidates:
        if span.start < cursor:
            continue
        non_overlapping.append(span)
        cursor = span.end
    return non_overlapping


class CryptographicPiiVaultAdapter:
    """Zero-trust PII redaction and envelope-encrypted vault with O(1) crypto-shredding."""

    def __init__(
        self,
        *,
        cipher: EnvelopeCipherProtocol | None = None,
        master_kek: bytes | None = None,
        blind_index_pepper: bytes | None = None,
    ) -> None:
        self._cipher: EnvelopeCipherProtocol = (
            cipher if cipher is not None else StdlibHmacCtrEnvelopeCipher()
        )
        # Ephemeral CSPRNG root key when KMS wrapping key is not injected (zero hardcoded secrets).
        self._root_kek: bytes = master_kek if master_kek is not None else os.urandom(32)
        if len(self._root_kek) != 32:
            raise ValueError("master_kek must be exactly 32 bytes.")
        self._blind_pepper: bytes = (
            blind_index_pepper if blind_index_pepper is not None else os.urandom(32)
        )
        self._records_by_ref: dict[tuple[UUID, UUID], _EncryptedVaultRecord] = {}
        self._ref_by_candidate: dict[tuple[UUID, UUID], UUID] = {}
        self._owner_by_ref: dict[UUID, UUID] = {}
        self._owner_by_candidate: dict[UUID, UUID] = {}

    def _derive_tenant_kek(self, tenant_id: TenantId, key_id: UUID) -> bytes:
        return hmac.new(
            self._root_kek,
            b"tenant-kek:" + tenant_id.value.bytes + key_id.bytes,
            hashlib.sha256,
        ).digest()

    @staticmethod
    def _build_aad(tenant_id: TenantId, candidate_id: UUID, vault_reference_id: UUID) -> bytes:
        return tenant_id.value.bytes + candidate_id.bytes + vault_reference_id.bytes

    def compute_blind_email_hmac(self, *, tenant_id: TenantId, email: str) -> str:
        """Compute deterministic blind HMAC-SHA256 index for duplicate detection (ADR-008)."""
        _require_tenant(tenant_id)
        normalized_email = email.strip().lower().encode("utf-8")
        return hmac.new(
            self._blind_pepper,
            tenant_id.value.bytes + b":" + normalized_email,
            hashlib.sha256,
        ).hexdigest()

    def get_audit_snapshot(
        self, *, tenant_id: TenantId, candidate_id: UUID
    ) -> VaultRecordAuditSnapshot | None:
        """Return non-PII storage inspection metadata for auditing & testing."""
        _require_tenant(tenant_id)
        ref = self._ref_by_candidate.get((tenant_id.value, candidate_id))
        if ref is None:
            return None
        rec = self._records_by_ref[(tenant_id.value, ref)]
        return VaultRecordAuditSnapshot(
            vault_reference_id=rec.vault_reference_id,
            tenant_id=rec.tenant_id,
            candidate_id=rec.candidate_id,
            key_id=rec.key_id,
            is_shredded=rec.is_shredded,
            has_ciphertext=bool(rec.encrypted_payload),
            has_wrapped_dek=bool(rec.wrapped_dek),
            blind_email_hmac_index=rec.blind_email_hmac_index,
            shredded_at=rec.shredded_at,
        )

    def _decrypt_record_secrets(
        self, tenant_id: TenantId, rec: _EncryptedVaultRecord
    ) -> dict[str, str]:
        aad = self._build_aad(tenant_id, rec.candidate_id, rec.vault_reference_id)
        tenant_kek = self._derive_tenant_kek(tenant_id, rec.key_id)
        dek = self._cipher.decrypt_with_aad(tenant_kek, rec.wrapped_dek, aad=aad)
        plaintext = self._cipher.decrypt_with_aad(dek, rec.encrypted_payload, aad=aad)
        loaded = json.loads(plaintext.decode("utf-8"))
        if not isinstance(loaded, dict):
            raise ExternalAdapterFailureError(
                "Corrupted PII vault JSON structure.",
                code="VAULT_INTEGRITY_ERROR",
                retryable=False,
            )
        return {str(k): str(v) for k, v in loaded.items()}

    async def tokenize_and_vault(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
        raw_text: str,
    ) -> PiiTokenizationOutcomeDTO:
        """Detect PII, encrypt under per-candidate DEK, and return surrogate tokens."""
        _require_tenant(tenant_id)
        owner = self._owner_by_candidate.get(candidate_profile_id)
        if owner is not None and owner != tenant_id.value:
            raise UnauthorizedTenantAccessError(
                f"Candidate {candidate_profile_id} belongs to another tenant.",
                code="CROSS_TENANT_ACCESS_DENIED",
            )

        existing_ref = self._ref_by_candidate.get((tenant_id.value, candidate_profile_id))
        existing_rec: _EncryptedVaultRecord | None = (
            self._records_by_ref.get((tenant_id.value, existing_ref))
            if existing_ref is not None
            else None
        )
        if existing_rec is not None and existing_rec.is_shredded:
            raise ResourceConflictError(
                f"Candidate {candidate_profile_id} PII vault was crypto-shredded.",
                code="PII_CRYPTO_SHREDDED",
            )

        spans = _detect_pii_spans(raw_text)
        existing_secrets: dict[str, str] = {}
        existing_cats: dict[str, str] = {}
        first_email: str | None = None
        if existing_rec is not None:
            existing_secrets = self._decrypt_record_secrets(tenant_id, existing_rec)
            existing_cats = dict(existing_rec.token_categories)

        # Preserve prior token assignments when Stage 3 + UseCase re-runs on the same candidate.
        value_to_token: dict[tuple[str, str], str] = {
            (existing_cats.get(tok, "OTHER"), val): tok
            for tok, val in existing_secrets.items()
        }
        counters: dict[str, int] = {}
        for tok in existing_secrets:
            prefix = tok.strip("[]")
            if prefix.startswith("PII_") and "_" in prefix[4:]:
                cat_part, num_part = prefix[4:].rsplit("_", 1)
                if num_part.isdigit():
                    counters[cat_part] = max(counters.get(cat_part, 0), int(num_part))

        redacted_parts: list[str] = []
        cursor = 0
        secrets_map: dict[str, str] = dict(existing_secrets)
        token_categories: dict[str, str] = dict(existing_cats)

        for span in spans:
            redacted_parts.append(raw_text[cursor : span.start])
            cache_key = (span.category, span.raw_value)
            token = value_to_token.get(cache_key)
            if token is None:
                tag = _TAG_BY_CATEGORY.get(span.category, span.category)
                next_idx = counters.get(tag, 0) + 1
                counters[tag] = next_idx
                token = f"[PII_{tag}_{next_idx}]"
                value_to_token[cache_key] = token
            secrets_map[token] = span.raw_value
            token_categories[token] = span.category
            if span.category == "EMAIL" and first_email is None:
                first_email = span.raw_value
            redacted_parts.append(token)
            cursor = span.end
        redacted_parts.append(raw_text[cursor:])
        anonymized_text = "".join(redacted_parts)

        vault_ref = (
            existing_rec.vault_reference_id
            if existing_rec is not None
            else generate_uuid7()
        )
        key_id = existing_rec.key_id if existing_rec is not None else generate_uuid7()
        aad = self._build_aad(tenant_id, candidate_profile_id, vault_ref)
        dek = self._cipher.generate_dek()
        tenant_kek = self._derive_tenant_kek(tenant_id, key_id)
        wrapped_dek = self._cipher.encrypt_with_aad(tenant_kek, dek, aad=aad)
        encrypted_payload = self._cipher.encrypt_with_aad(
            dek,
            json.dumps(secrets_map, sort_keys=True).encode("utf-8"),
            aad=aad,
        )
        blind_hmac = (
            self.compute_blind_email_hmac(tenant_id=tenant_id, email=first_email)
            if first_email is not None
            else (existing_rec.blind_email_hmac_index if existing_rec is not None else None)
        )

        record = _EncryptedVaultRecord(
            vault_reference_id=vault_ref,
            tenant_id=tenant_id.value,
            candidate_id=candidate_profile_id,
            key_id=key_id,
            wrapped_dek=wrapped_dek,
            encrypted_payload=encrypted_payload,
            token_categories=tuple(token_categories.items()),
            blind_email_hmac_index=blind_hmac,
        )
        self._records_by_ref[(tenant_id.value, vault_ref)] = record
        self._ref_by_candidate[(tenant_id.value, candidate_profile_id)] = vault_ref
        self._owner_by_ref[vault_ref] = tenant_id.value
        self._owner_by_candidate[candidate_profile_id] = tenant_id.value

        return PiiTokenizationOutcomeDTO(
            anonymized_text=anonymized_text,
            pii_redaction_map=PiiRedactionMap.from_mapping(
                vault_reference_id=vault_ref,
                token_categories=token_categories,
            ),
            vault_reference_id=vault_ref,
        )

    async def detokenize(
        self,
        *,
        tenant_id: TenantId,
        vault_reference_id: UUID,
    ) -> Mapping[str, str]:
        """Authorize tenant ownership, unwrap DEK, and return token -> raw PII mapping."""
        _require_tenant(tenant_id)
        owner = self._owner_by_ref.get(vault_reference_id)
        if owner is not None and owner != tenant_id.value:
            raise UnauthorizedTenantAccessError(
                f"Vault reference {vault_reference_id} belongs to another tenant.",
                code="CROSS_TENANT_ACCESS_DENIED",
            )
        rec = self._records_by_ref.get((tenant_id.value, vault_reference_id))
        if rec is None:
            raise ResourceNotFoundError(
                f"PII vault record {vault_reference_id} not found.",
                code="PII_VAULT_RECORD_NOT_FOUND",
            )
        if rec.is_shredded:
            raise ResourceConflictError(
                f"PII vault record {vault_reference_id} has been crypto-shredded.",
                code="PII_CRYPTO_SHREDDED",
            )
        return MappingProxyType(self._decrypt_record_secrets(tenant_id, rec))

    async def crypto_shred(
        self,
        *,
        tenant_id: TenantId,
        candidate_id: UUID,
        reason: str,
    ) -> CryptoShredReceiptDTO:
        """O(1) GDPR erasure: destroy wrapped DEK & encrypted PII payload permanently."""
        _require_tenant(tenant_id)
        owner = self._owner_by_candidate.get(candidate_id)
        if owner is not None and owner != tenant_id.value:
            raise UnauthorizedTenantAccessError(
                f"Candidate {candidate_id} belongs to another tenant.",
                code="CROSS_TENANT_ACCESS_DENIED",
            )
        ref = self._ref_by_candidate.get((tenant_id.value, candidate_id))
        if ref is None:
            now = utc_now()
            return CryptoShredReceiptDTO(
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                shredded_at=now,
                already_shredded=True,
            )
        rec = self._records_by_ref[(tenant_id.value, ref)]
        if rec.is_shredded:
            return CryptoShredReceiptDTO(
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                shredded_at=rec.shredded_at or utc_now(),
                already_shredded=True,
            )

        now = utc_now()
        # Cryptographic key destruction: permanently wipe wrapped DEK, ciphertext, and blind index.
        rec.wrapped_dek = b""
        rec.encrypted_payload = b""
        rec.blind_email_hmac_index = None
        rec.is_shredded = True
        rec.shredded_at = now
        rec.erasure_reason = reason
        return CryptoShredReceiptDTO(
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            shredded_at=now,
            already_shredded=False,
        )
