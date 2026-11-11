"""Unit tests for Phase 3 Virus Scanner, Cryptographic PII Vault, and Object Storage."""

from __future__ import annotations

import asyncio
import io
import zipfile
from collections.abc import Coroutine
from typing import Any
from xml.sax.saxutils import escape

import pytest

from talent_screener.application.common.exceptions import (
    ExternalAdapterFailureError,
    ResourceConflictError,
    ResourceNotFoundError,
    UnauthorizedTenantAccessError,
)
from talent_screener.domain.shared import SHA256Hash, TenantId, generate_uuid7
from talent_screener.infrastructure.persistence import TenantScopedObjectStorageAdapter
from talent_screener.infrastructure.security import (
    CryptographicPiiVaultAdapter,
    SignatureAndHeuristicVirusScannerAdapter,
    StdlibHmacCtrEnvelopeCipher,
)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _build_minimal_docx(
    paragraphs: tuple[str, ...],
    *,
    extra_entry: tuple[str, bytes] | None = None,
) -> bytes:
    buf = io.BytesIO()
    body_xml = "".join(
        f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs
    )
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}</w:body></w:document>"
    ).encode()
    content_types = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="xml" ContentType="application/xml"/></Types>'
    )
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("word/document.xml", doc_xml)
        if extra_entry is not None:
            zf.writestr(extra_entry[0], extra_entry[1])
    return buf.getvalue()


@pytest.mark.unit
def test_virus_scanner_accepts_valid_pdf_docx_text_and_rejects_threats() -> None:
    async def _run_test() -> None:
        scanner = SignatureAndHeuristicVirusScannerAdapter(max_file_size_bytes=4096)
        tid = TenantId.generate()

        # 1. Valid PDF (including mention of Node/JavaScript text which is not active script key)
        pdf_payload = b"%PDF-1.7\nSUMMARY\nSenior Engineer using Node/JavaScript and Python."
        pdf_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="resume.pdf",
            payload=pdf_payload,
            expected_sha256=SHA256Hash.from_bytes(pdf_payload),
        )
        assert pdf_out.is_clean
        assert pdf_out.verified_mime_type == "application/pdf"

        # 2. Valid DOCX container
        docx_payload = _build_minimal_docx(("EXPERIENCE", "Python engineer 2021-2024"))
        docx_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="candidate.docx",
            payload=docx_payload,
            expected_sha256=SHA256Hash.from_bytes(docx_payload),
        )
        assert docx_out.is_clean
        assert docx_out.verified_mime_type == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

        # 3. Valid UTF-8 Markdown / plain text
        md_payload = b"# Summary\nDistributed Systems & Python engineer."
        md_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="profile.md",
            payload=md_payload,
            expected_sha256=SHA256Hash.from_bytes(md_payload),
        )
        assert md_out.is_clean
        assert md_out.verified_mime_type == "text/markdown"

        # 4. EICAR malware payload rejected
        eicar_payload = b"%PDF-1.7 EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
        eicar_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="eicar.pdf",
            payload=eicar_payload,
            expected_sha256=SHA256Hash.from_bytes(eicar_payload),
        )
        assert not eicar_out.is_clean
        assert eicar_out.reason_code == "MALWARE_DETECTED"

        # 5. Oversized payload rejected
        oversized = b"%PDF-1.7 " + (b"A" * 5000)
        size_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="huge.pdf",
            payload=oversized,
            expected_sha256=SHA256Hash.from_bytes(oversized),
        )
        assert not size_out.is_clean
        assert size_out.reason_code == "PAYLOAD_TOO_LARGE"

        # 6. PDF active JavaScript OpenAction rejected
        malicious_pdf = b"%PDF-1.7\n1 0 obj << /OpenAction /JavaScript (app.alert(1)) >> endobj"
        js_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="active.pdf",
            payload=malicious_pdf,
            expected_sha256=SHA256Hash.from_bytes(malicious_pdf),
        )
        assert not js_out.is_clean
        assert js_out.reason_code == "MALICIOUS_ACTIVE_CONTENT"

        # 7. Empty stream rejected
        empty_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="empty.pdf",
            payload=b"",
            expected_sha256=SHA256Hash.from_bytes(b""),
        )
        assert not empty_out.is_clean
        assert empty_out.reason_code == "EMPTY_OR_CORRUPT_FILE"

        # 8. Magic byte spoofing (plain text claiming .pdf extension) rejected
        spoof = b"Plain text disguised as PDF"
        spoof_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="spoof.pdf",
            payload=spoof,
            expected_sha256=SHA256Hash.from_bytes(spoof),
        )
        assert not spoof_out.is_clean
        assert spoof_out.reason_code == "INVALID_MAGIC_BYTES"

        # 9. SHA-256 checksum mismatch rejected
        mismatch_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="resume.pdf",
            payload=pdf_payload,
            expected_sha256=SHA256Hash.from_bytes(b"different"),
        )
        assert not mismatch_out.is_clean
        assert mismatch_out.reason_code == "CHECKSUM_MISMATCH"

        # 10. DOCX containing VBA macro rejected + Zip Slip path traversal rejected
        macro_docx = _build_minimal_docx(
            ("Resume",), extra_entry=("word/vbaProject.bin", b"macro-bytes")
        )
        macro_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="macro.docx",
            payload=macro_docx,
            expected_sha256=SHA256Hash.from_bytes(macro_docx),
        )
        assert not macro_out.is_clean
        assert macro_out.reason_code == "MALICIOUS_MACRO_DETECTED"

        slip_docx = _build_minimal_docx(("Resume",), extra_entry=("../../evil.txt", b"pwn"))
        slip_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="slip.docx",
            payload=slip_docx,
            expected_sha256=SHA256Hash.from_bytes(slip_docx),
        )
        assert not slip_out.is_clean
        assert slip_out.reason_code == "ZIP_SLIP_PATH_TRAVERSAL"

        # 11. Disallowed executable extension & missing tenant context
        exe_out = await scanner.scan_bytes(
            tenant_id=tid,
            filename="payload.exe",
            payload=b"MZ\x90\x00",
            expected_sha256=SHA256Hash.from_bytes(b"MZ\x90\x00"),
        )
        assert not exe_out.is_clean
        assert exe_out.reason_code == "UNSUPPORTED_FILE_TYPE"

        with pytest.raises(UnauthorizedTenantAccessError):
            await scanner.scan_bytes(
                tenant_id=None,  # type: ignore[arg-type]
                filename="resume.pdf",
                payload=pdf_payload,
                expected_sha256=SHA256Hash.from_bytes(pdf_payload),
            )

    _run(_run_test())


@pytest.mark.unit
def test_cryptographic_pii_vault_tokenization_isolation_and_crypto_shred() -> None:
    async def _run_test() -> None:
        vault = CryptographicPiiVaultAdapter()
        tid_a = TenantId.generate()
        tid_b = TenantId.generate()
        cand_id = generate_uuid7()

        raw_cv = (
            "Name: Alice Lovelace\n"
            "Email: alice.lovelace@example.com\n"
            "Backup: alice.lovelace@example.com\n"
            "Phone: +1-555-867-5309\n"
            "Profile: https://github.com/alovelace and linkedin.com/in/alovelace\n"
            "National ID: 123-45-6789\n"
            "Address: 742 Evergreen Terrace, Austin TX\n"
            "EXPERIENCE\n"
            "Senior Python Engineer (2021-01 to 2024-12)\n"
            "Built Python asyncio pipelines handling 25,000 req/sec.\n"
        )
        outcome = await vault.tokenize_and_vault(
            tenant_id=tid_a,
            candidate_profile_id=cand_id,
            raw_text=raw_cv,
        )
        assert "[PII_NAME_1]" in outcome.anonymized_text
        assert "[PII_EMAIL_1]" in outcome.anonymized_text
        assert outcome.anonymized_text.count("[PII_EMAIL_1]") == 2
        assert "[PII_PHONE_1]" in outcome.anonymized_text
        assert "[PII_URL_1]" in outcome.anonymized_text
        assert "[PII_URL_2]" in outcome.anonymized_text
        assert "[PII_NATIONAL_ID_1]" in outcome.anonymized_text
        assert "[PII_ADDRESS_1]" in outcome.anonymized_text
        assert "alice.lovelace@example.com" not in outcome.anonymized_text
        assert "555-867-5309" not in outcome.anonymized_text
        assert "123-45-6789" not in outcome.anonymized_text
        # Work dates and engineering numbers remain preserved cleanly.
        assert "2021-01 to 2024-12" in outcome.anonymized_text
        assert "25,000 req/sec" in outcome.anonymized_text

        # Authorized detokenization restores original PII accurately.
        restored = await vault.detokenize(
            tenant_id=tid_a,
            vault_reference_id=outcome.vault_reference_id,
        )
        assert restored["[PII_EMAIL_1]"] == "alice.lovelace@example.com"
        assert restored["[PII_PHONE_1]"] == "+1-555-867-5309"
        assert restored["[PII_NATIONAL_ID_1]"] == "123-45-6789"

        # Calling tokenize_and_vault a second time with already-redacted text preserves secrets.
        idempotent_outcome = await vault.tokenize_and_vault(
            tenant_id=tid_a,
            candidate_profile_id=cand_id,
            raw_text=outcome.anonymized_text,
        )
        assert idempotent_outcome.vault_reference_id == outcome.vault_reference_id
        restored_after = await vault.detokenize(
            tenant_id=tid_a,
            vault_reference_id=idempotent_outcome.vault_reference_id,
        )
        assert restored_after["[PII_EMAIL_1]"] == "alice.lovelace@example.com"

        # Blind email HMAC differs across tenants.
        h_a = vault.compute_blind_email_hmac(tenant_id=tid_a, email="Alice.Lovelace@Example.com")
        h_b = vault.compute_blind_email_hmac(tenant_id=tid_b, email="Alice.Lovelace@Example.com")
        assert h_a != h_b
        snap = vault.get_audit_snapshot(tenant_id=tid_a, candidate_id=cand_id)
        assert snap is not None
        assert snap.blind_email_hmac_index == h_a
        assert snap.has_ciphertext and snap.has_wrapped_dek and not snap.is_shredded

        # Cross-tenant detokenize and unknown ref denied.
        with pytest.raises(UnauthorizedTenantAccessError):
            await vault.detokenize(tenant_id=tid_b, vault_reference_id=outcome.vault_reference_id)
        with pytest.raises(ResourceNotFoundError):
            await vault.detokenize(tenant_id=tid_a, vault_reference_id=generate_uuid7())

        # O(1) GDPR crypto-shred destroys DEK & ciphertext permanently.
        receipt = await vault.crypto_shred(
            tenant_id=tid_a,
            candidate_id=cand_id,
            reason="GDPR Art. 17 erasure request",
        )
        assert not receipt.already_shredded
        shredded_snap = vault.get_audit_snapshot(tenant_id=tid_a, candidate_id=cand_id)
        assert shredded_snap is not None
        assert shredded_snap.is_shredded
        assert not shredded_snap.has_ciphertext
        assert not shredded_snap.has_wrapped_dek
        assert shredded_snap.blind_email_hmac_index is None

        # Detokenizing shredded record fails deterministically.
        with pytest.raises(ResourceConflictError) as exc_info:
            await vault.detokenize(tenant_id=tid_a, vault_reference_id=outcome.vault_reference_id)
        assert exc_info.value.code == "PII_CRYPTO_SHREDDED"

        # Re-shredding is idempotent.
        second_receipt = await vault.crypto_shred(
            tenant_id=tid_a,
            candidate_id=cand_id,
            reason="duplicate request",
        )
        assert second_receipt.already_shredded

        # Cipher tamper detection test
        cipher = StdlibHmacCtrEnvelopeCipher()
        key = cipher.generate_dek()
        ct = cipher.encrypt_with_aad(key, b"secret-pii", aad=b"tenant-aad")
        assert cipher.decrypt_with_aad(key, ct, aad=b"tenant-aad") == b"secret-pii"
        tampered = ct[:-1] + bytes([ct[-1] ^ 0xFF])
        with pytest.raises(ExternalAdapterFailureError):
            cipher.decrypt_with_aad(key, tampered, aad=b"tenant-aad")

    _run(_run_test())


@pytest.mark.unit
def test_tenant_scoped_object_storage_isolation_and_sha256_integrity() -> None:
    async def _run_test() -> None:
        storage = TenantScopedObjectStorageAdapter()
        tid_a = TenantId.generate()
        tid_b = TenantId.generate()
        batch_id = generate_uuid7()
        payload = b"%PDF-1.7 sample resume bytes"
        digest = SHA256Hash.from_bytes(payload)

        key = storage.build_content_addressed_key(
            tenant_id=tid_a,
            batch_id=batch_id,
            content_sha256=digest,
        )
        assert key.startswith(f"{tid_a.value}/{batch_id}/")

        url = await storage.generate_presigned_upload_url(
            tenant_id=tid_a,
            object_key=key,
            content_sha256=digest,
            content_type="application/pdf",
        )
        assert digest.value in url and "sig=" in url

        stored_hash = await storage.put_verified_bytes(
            tenant_id=tid_a,
            object_key=key,
            payload=payload,
            content_type="application/pdf",
            expected_sha256=digest,
        )
        assert stored_hash == digest
        assert await storage.get_bytes(tenant_id=tid_a, object_key=key) == payload

        # Cross-tenant read must raise UnauthorizedTenantAccessError
        with pytest.raises(UnauthorizedTenantAccessError):
            await storage.get_bytes(tenant_id=tid_b, object_key=key)

        # Traversal keys rejected
        with pytest.raises(UnauthorizedTenantAccessError):
            await storage.put_bytes(
                tenant_id=tid_a,
                object_key="../escape/resume.pdf",
                payload=payload,
                content_type="application/pdf",
            )

        # Wrong expected SHA-256 rejected
        with pytest.raises(ExternalAdapterFailureError):
            await storage.put_verified_bytes(
                tenant_id=tid_a,
                object_key=key,
                payload=payload,
                content_type="application/pdf",
                expected_sha256=SHA256Hash.from_bytes(b"other"),
            )

        await storage.delete_object(tenant_id=tid_a, object_key=key)
        with pytest.raises(ResourceNotFoundError):
            await storage.get_bytes(tenant_id=tid_a, object_key=key)

    _run(_run_test())
