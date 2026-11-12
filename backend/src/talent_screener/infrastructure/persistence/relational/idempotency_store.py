"""SQL-backed Idempotency Store and ADR-008 Cryptographic PII Vault Stores."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from talent_screener.application.common.dtos import (
    CryptoShredReceiptDTO,
    IdempotencyRecordDTO,
    IdempotencyStatus,
    PiiTokenizationOutcomeDTO,
)
from talent_screener.application.common.exceptions import (
    IdempotencyConflictError,
    ResourceConflictError,
    ResourceNotFoundError,
    UnauthorizedTenantAccessError,
)
from talent_screener.domain.contexts.document_intelligence.value_objects import PiiRedactionMap
from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.persistence.relational.schema import SqlDatabaseManager
from talent_screener.infrastructure.security.pii_vault import (
    _TAG_BY_CATEGORY,
    CryptographicPiiVaultAdapter,
    EnvelopeCipherProtocol,
    VaultRecordAuditSnapshot,
    _detect_pii_spans,
    _EncryptedVaultRecord,
)


def _require_tenant(tenant_id: object, *, label: str) -> TenantId:
    if not isinstance(tenant_id, TenantId) or not isinstance(tenant_id.value, UUID):
        raise UnauthorizedTenantAccessError(
            f"Valid TenantId is required for {label}.",
            code="MISSING_TENANT_CONTEXT",
        )
    return tenant_id


class SqlIdempotencyStoreAdapter:
    """SQL-backed IdempotencyStorePort enforcing atomic mutation deduplication and replay."""

    def __init__(self, db: SqlDatabaseManager) -> None:
        self._db = db

    async def check_and_reserve(
        self,
        *,
        tenant_id: TenantId,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> IdempotencyRecordDTO | None:
        tenant = _require_tenant(tenant_id, label="idempotency check_and_reserve")
        t_str = str(tenant.value)
        now_iso = utc_now().isoformat()

        with self._db.write_transaction():
            existing = self._db.fetchone(
                "SELECT payload_hash, status, resource_id, serialized_result_json "
                "FROM idempotency_records "
                "WHERE tenant_id = ? AND operation = ? AND idempotency_key = ?",
                (t_str, operation, idempotency_key),
            )
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash:
                    raise IdempotencyConflictError(
                        "Idempotency key reused with mismatched request payload.",
                    )
                res_raw = existing["serialized_result_json"]
                parsed_res = (
                    dict(json.loads(str(res_raw))) if res_raw is not None else None
                )
                return IdempotencyRecordDTO(
                    tenant_id=tenant,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    payload_hash=str(existing["payload_hash"]),
                    status=IdempotencyStatus(str(existing["status"])),
                    resource_id=(
                        UUID(str(existing["resource_id"]))
                        if existing["resource_id"] is not None
                        else None
                    ),
                    serialized_result=parsed_res,
                )

            try:
                self._db.execute(
                    "INSERT INTO idempotency_records ("
                    "tenant_id, operation, idempotency_key, payload_hash, status, "
                    "resource_id, serialized_result_json, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                    (
                        t_str,
                        operation,
                        idempotency_key,
                        payload_hash,
                        IdempotencyStatus.IN_PROGRESS.value,
                        now_iso,
                        now_iso,
                    ),
                )
            except sqlite3.IntegrityError:
                row = self._db.fetchone(
                    "SELECT payload_hash, status, resource_id, serialized_result_json "
                    "FROM idempotency_records "
                    "WHERE tenant_id = ? AND operation = ? AND idempotency_key = ?",
                    (t_str, operation, idempotency_key),
                )
                assert row is not None
                if str(row["payload_hash"]) != payload_hash:
                    raise IdempotencyConflictError(
                        "Idempotency key reused with mismatched request payload.",
                    ) from None
                res_raw = row["serialized_result_json"]
                return IdempotencyRecordDTO(
                    tenant_id=tenant,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    payload_hash=str(row["payload_hash"]),
                    status=IdempotencyStatus(str(row["status"])),
                    resource_id=(
                        UUID(str(row["resource_id"]))
                        if row["resource_id"] is not None
                        else None
                    ),
                    serialized_result=(
                        dict(json.loads(str(res_raw))) if res_raw is not None else None
                    ),
                )
        return None

    async def mark_completed(
        self,
        *,
        tenant_id: TenantId,
        operation: str,
        idempotency_key: str,
        resource_id: UUID,
        serialized_result: Mapping[str, object] | None = None,
    ) -> None:
        tenant = _require_tenant(tenant_id, label="idempotency mark_completed")
        result_json = (
            json.dumps(dict(serialized_result), separators=(",", ":"), sort_keys=True)
            if serialized_result is not None
            else None
        )
        with self._db.write_transaction():
            self._db.execute(
                "UPDATE idempotency_records "
                "SET status = ?, resource_id = ?, serialized_result_json = ?, updated_at = ? "
                "WHERE tenant_id = ? AND operation = ? AND idempotency_key = ?",
                (
                    IdempotencyStatus.COMPLETED.value,
                    str(resource_id),
                    result_json,
                    utc_now().isoformat(),
                    str(tenant.value),
                    operation,
                    idempotency_key,
                ),
            )

    async def release_reservation(
        self,
        *,
        tenant_id: TenantId,
        operation: str,
        idempotency_key: str,
    ) -> None:
        tenant = _require_tenant(tenant_id, label="idempotency release_reservation")
        with self._db.write_transaction():
            self._db.execute(
                "DELETE FROM idempotency_records "
                "WHERE tenant_id = ? AND operation = ? AND idempotency_key = ? "
                "AND status = ?",
                (
                    str(tenant.value),
                    operation,
                    idempotency_key,
                    IdempotencyStatus.IN_PROGRESS.value,
                ),
            )


class SqlCandidatePiiVaultStore(CryptographicPiiVaultAdapter):
    """Durable SQL-backed ADR-008 Cryptographic PII Vault implementing PiiVaultPort."""

    def __init__(
        self,
        db: SqlDatabaseManager,
        *,
        cipher: EnvelopeCipherProtocol | None = None,
        master_kek: bytes | None = None,
        blind_index_pepper: bytes | None = None,
    ) -> None:
        super().__init__(
            cipher=cipher,
            master_kek=master_kek,
            blind_index_pepper=blind_index_pepper,
        )
        self._db = db

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> _EncryptedVaultRecord:
        cats = tuple(
            (str(tok), str(cat))
            for tok, cat in json.loads(str(row["token_categories_json"]))
        )
        shredded_raw = row["shredded_at"]
        return _EncryptedVaultRecord(
            vault_reference_id=UUID(str(row["vault_reference_id"])),
            tenant_id=UUID(str(row["tenant_id"])),
            candidate_id=UUID(str(row["candidate_id"])),
            key_id=UUID(str(row["key_id"])),
            wrapped_dek=bytes(row["wrapped_dek"]),
            encrypted_payload=bytes(row["encrypted_payload"]),
            token_categories=cats,
            blind_email_hmac_index=(
                str(row["blind_email_hmac_index"])
                if row["blind_email_hmac_index"] is not None
                else None
            ),
            is_shredded=bool(row["is_shredded"]),
            shredded_at=(
                datetime.fromisoformat(str(shredded_raw))
                if shredded_raw is not None
                else None
            ),
            erasure_reason=(
                str(row["erasure_reason"]) if row["erasure_reason"] is not None else None
            ),
        )

    def _fetch_by_candidate(
        self, tenant_id: TenantId, candidate_id: UUID
    ) -> _EncryptedVaultRecord | None:
        cross = self._db.fetchone(
            "SELECT * FROM candidate_pii_vault WHERE candidate_id = ?",
            (str(candidate_id),),
        )
        if cross is not None and UUID(str(cross["tenant_id"])) != tenant_id.value:
            raise UnauthorizedTenantAccessError(
                f"Candidate {candidate_id} belongs to another tenant.",
                code="CROSS_TENANT_ACCESS_DENIED",
            )
        row = self._db.fetchone(
            "SELECT * FROM candidate_pii_vault WHERE tenant_id = ? AND candidate_id = ?",
            (str(tenant_id.value), str(candidate_id)),
        )
        return self._row_to_record(row) if row is not None else None

    def _fetch_by_ref(
        self, tenant_id: TenantId, vault_reference_id: UUID
    ) -> _EncryptedVaultRecord | None:
        cross = self._db.fetchone(
            "SELECT * FROM candidate_pii_vault WHERE vault_reference_id = ?",
            (str(vault_reference_id),),
        )
        if cross is not None and UUID(str(cross["tenant_id"])) != tenant_id.value:
            raise UnauthorizedTenantAccessError(
                f"Vault reference {vault_reference_id} belongs to another tenant.",
                code="CROSS_TENANT_ACCESS_DENIED",
            )
        row = self._db.fetchone(
            "SELECT * FROM candidate_pii_vault "
            "WHERE tenant_id = ? AND vault_reference_id = ?",
            (str(tenant_id.value), str(vault_reference_id)),
        )
        return self._row_to_record(row) if row is not None else None

    def get_audit_snapshot(
        self, *, tenant_id: TenantId, candidate_id: UUID
    ) -> VaultRecordAuditSnapshot | None:
        _require_tenant(tenant_id, label="PII Vault audit snapshot")
        rec = self._fetch_by_candidate(tenant_id, candidate_id)
        if rec is None:
            return None
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

    async def tokenize_and_vault(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
        raw_text: str,
    ) -> PiiTokenizationOutcomeDTO:
        _require_tenant(tenant_id, label="PII Vault access")
        existing_rec = self._fetch_by_candidate(tenant_id, candidate_profile_id)
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
        secrets_map = dict(existing_secrets)
        token_categories = dict(existing_cats)
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
        cats_json = json.dumps(list(token_categories.items()), separators=(",", ":"))

        with self._db.write_transaction():
            if existing_rec is None:
                self._db.execute(
                    "INSERT INTO candidate_pii_vault ("
                    "vault_reference_id, tenant_id, candidate_id, key_id, wrapped_dek, "
                    "encrypted_payload, token_categories_json, blind_email_hmac_index, "
                    "is_shredded, shredded_at, erasure_reason"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)",
                    (
                        str(vault_ref),
                        str(tenant_id.value),
                        str(candidate_profile_id),
                        str(key_id),
                        wrapped_dek,
                        encrypted_payload,
                        cats_json,
                        blind_hmac,
                    ),
                )
            else:
                self._db.execute(
                    "UPDATE candidate_pii_vault SET "
                    "wrapped_dek = ?, encrypted_payload = ?, token_categories_json = ?, "
                    "blind_email_hmac_index = ? "
                    "WHERE tenant_id = ? AND vault_reference_id = ?",
                    (
                        wrapped_dek,
                        encrypted_payload,
                        cats_json,
                        blind_hmac,
                        str(tenant_id.value),
                        str(vault_ref),
                    ),
                )

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
        _require_tenant(tenant_id, label="PII Vault access")
        rec = self._fetch_by_ref(tenant_id, vault_reference_id)
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
        _require_tenant(tenant_id, label="PII Vault access")
        rec = self._fetch_by_candidate(tenant_id, candidate_id)
        if rec is None:
            return CryptoShredReceiptDTO(
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                shredded_at=utc_now(),
                already_shredded=True,
            )
        if rec.is_shredded:
            return CryptoShredReceiptDTO(
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                shredded_at=rec.shredded_at or utc_now(),
                already_shredded=True,
            )
        now = utc_now()
        with self._db.write_transaction():
            self._db.execute(
                "UPDATE candidate_pii_vault SET "
                "wrapped_dek = ?, encrypted_payload = ?, blind_email_hmac_index = NULL, "
                "is_shredded = 1, shredded_at = ?, erasure_reason = ? "
                "WHERE tenant_id = ? AND candidate_id = ?",
                (
                    b"",
                    b"",
                    now.isoformat(),
                    reason,
                    str(tenant_id.value),
                    str(candidate_id),
                ),
            )
        return CryptoShredReceiptDTO(
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            shredded_at=now,
            already_shredded=False,
        )
