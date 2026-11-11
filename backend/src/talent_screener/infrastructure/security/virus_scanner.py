"""Stage 1 Security & Malware Quarantine Adapter enforcing magic-byte and container guards."""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from uuid import UUID

from talent_screener.application.common.dtos import VirusScanOutcomeDTO
from talent_screener.application.common.exceptions import UnauthorizedTenantAccessError
from talent_screener.domain.contexts.document_intelligence.value_objects import (
    MAX_CV_FILE_SIZE_BYTES,
)
from talent_screener.domain.shared.value_objects import SHA256Hash, TenantId

_EICAR_SIGNATURES: tuple[bytes, ...] = (
    b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE",
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR",
)

_PDF_ACTIVE_CONTENT_RE = re.compile(
    rb"(?:<<[^>]{0,256}/(?:JavaScript|JS|OpenAction|Launch|EmbeddedFile)\b"
    rb"|/S\s*/(?:JavaScript|Launch)\b)"
)

_EXECUTABLE_PREFIXES: tuple[bytes, ...] = (
    b"MZ",
    b"\x7fELF",
    b"\xca\xfe\xba\xbe",
    b"\xcf\xfa\xed\xfe",
    b"#!",
)

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".txt", ".md"})


def _require_tenant(tenant_id: object) -> TenantId:
    if not isinstance(tenant_id, TenantId) or not isinstance(tenant_id.value, UUID):
        raise UnauthorizedTenantAccessError(
            "Valid TenantId is required for virus scanning.",
            code="MISSING_TENANT_CONTEXT",
        )
    return tenant_id


def _extract_extension(filename: str) -> str:
    clean_name = filename.replace("\\", "/").split("/")[-1].strip().lower()
    if "." not in clean_name:
        return ""
    return f".{clean_name.rsplit('.', 1)[-1]}"


@dataclass(frozen=True, slots=True)
class _ContainerCheckResult:
    is_clean: bool
    verified_mime_type: str
    detected_signature: str | None = None
    reason_code: str | None = None


class SignatureAndHeuristicVirusScannerAdapter:
    """Infrastructure VirusScannerPort adapter checking magic bytes, archives, and threats."""

    def __init__(
        self,
        *,
        max_file_size_bytes: int = MAX_CV_FILE_SIZE_BYTES,
        max_zip_uncompressed_bytes: int = 25 * 1024 * 1024,
        max_zip_entries: int = 512,
        extra_malware_signatures: tuple[bytes, ...] = (),
    ) -> None:
        self._max_file_size_bytes = max_file_size_bytes
        self._max_zip_uncompressed_bytes = max_zip_uncompressed_bytes
        self._max_zip_entries = max_zip_entries
        self._malware_signatures = _EICAR_SIGNATURES + tuple(extra_malware_signatures)

    def _inspect_malware_signatures(self, payload: bytes) -> str | None:
        upper_slice = payload[: min(len(payload), 1024 * 1024)]
        for sig in self._malware_signatures:
            if sig in payload or sig.upper() in upper_slice.upper():
                return sig.decode("ascii", errors="replace")[:64]
        return None

    def _inspect_pdf(self, payload: bytes) -> _ContainerCheckResult:
        if not payload.startswith(b"%PDF-"):
            return _ContainerCheckResult(
                is_clean=False,
                verified_mime_type="application/octet-stream",
                detected_signature="INVALID_PDF_HEADER",
                reason_code="INVALID_MAGIC_BYTES",
            )
        if _PDF_ACTIVE_CONTENT_RE.search(payload):
            return _ContainerCheckResult(
                is_clean=False,
                verified_mime_type="application/pdf",
                detected_signature="PDF_ACTIVE_SCRIPT_HEURISTIC",
                reason_code="MALICIOUS_ACTIVE_CONTENT",
            )
        return _ContainerCheckResult(is_clean=True, verified_mime_type="application/pdf")

    def _inspect_docx(self, payload: bytes) -> _ContainerCheckResult:
        if not payload.startswith(b"PK\x03\x04"):
            return _ContainerCheckResult(
                is_clean=False,
                verified_mime_type="application/octet-stream",
                detected_signature="INVALID_DOCX_HEADER",
                reason_code="INVALID_MAGIC_BYTES",
            )
        try:
            with zipfile.ZipFile(io.BytesIO(payload), mode="r") as archive:
                infos = archive.infolist()
                if not infos:
                    return _ContainerCheckResult(
                        is_clean=False,
                        verified_mime_type="application/octet-stream",
                        detected_signature="EMPTY_ZIP_CONTAINER",
                        reason_code="CORRUPT_DOCX_CONTAINER",
                    )
                if len(infos) > self._max_zip_entries:
                    return _ContainerCheckResult(
                        is_clean=False,
                        verified_mime_type="application/octet-stream",
                        detected_signature="ZIP_ENTRY_LIMIT_EXCEEDED",
                        reason_code="ZIP_BOMB_DETECTED",
                    )
                total_uncompressed = 0
                entry_names: set[str] = set()
                for info in infos:
                    member_name = info.filename
                    if (
                        "\x00" in member_name
                        or member_name.startswith(("/", "\\"))
                        or "\\" in member_name
                        or ".." in posixpath.normpath(member_name).split("/")
                        or "/../" in f"/{member_name}"
                    ):
                        return _ContainerCheckResult(
                            is_clean=False,
                            verified_mime_type="application/octet-stream",
                            detected_signature="ZIP_PATH_TRAVERSAL",
                            reason_code="ZIP_SLIP_PATH_TRAVERSAL",
                        )
                    total_uncompressed += max(0, info.file_size)
                    if total_uncompressed > self._max_zip_uncompressed_bytes:
                        return _ContainerCheckResult(
                            is_clean=False,
                            verified_mime_type="application/octet-stream",
                            detected_signature="ZIP_UNCOMPRESSED_SIZE_EXCEEDED",
                            reason_code="ZIP_BOMB_DETECTED",
                        )
                    lowered = member_name.lower()
                    if lowered.endswith("vbaproject.bin") or lowered.endswith(".docm"):
                        return _ContainerCheckResult(
                            is_clean=False,
                            verified_mime_type=(
                                "application/vnd.openxmlformats-officedocument."
                                "wordprocessingml.document"
                            ),
                            detected_signature="DOCX_VBA_MACRO",
                            reason_code="MALICIOUS_MACRO_DETECTED",
                        )
                    entry_names.add(member_name)

                if (
                    "word/document.xml" not in entry_names
                    and "[Content_Types].xml" not in entry_names
                ):
                    return _ContainerCheckResult(
                        is_clean=False,
                        verified_mime_type="application/octet-stream",
                        detected_signature="MISSING_OOXML_PARTS",
                        reason_code="CORRUPT_DOCX_CONTAINER",
                    )
        except (zipfile.BadZipFile, ValueError, RuntimeError):
            return _ContainerCheckResult(
                is_clean=False,
                verified_mime_type="application/octet-stream",
                detected_signature="CORRUPT_ZIP_ARCHIVE",
                reason_code="CORRUPT_DOCX_CONTAINER",
            )

        return _ContainerCheckResult(
            is_clean=True,
            verified_mime_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        )

    def _inspect_plain_text(self, payload: bytes, ext: str) -> _ContainerCheckResult:
        if any(payload.startswith(prefix) for prefix in _EXECUTABLE_PREFIXES) or payload.startswith(
            (b"%PDF-", b"PK\x03\x04")
        ):
            return _ContainerCheckResult(
                is_clean=False,
                verified_mime_type="application/octet-stream",
                detected_signature="POLYGLOT_TEXT_HEADER",
                reason_code="INVALID_MAGIC_BYTES",
            )
        if b"\x00" in payload:
            return _ContainerCheckResult(
                is_clean=False,
                verified_mime_type="application/octet-stream",
                detected_signature="BINARY_NULL_BYTES",
                reason_code="INVALID_MAGIC_BYTES",
            )
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError:
            return _ContainerCheckResult(
                is_clean=False,
                verified_mime_type="application/octet-stream",
                detected_signature="NON_UTF8_TEXT",
                reason_code="INVALID_TEXT_ENCODING",
            )
        if not decoded.strip():
            return _ContainerCheckResult(
                is_clean=False,
                verified_mime_type="text/plain",
                detected_signature="BLANK_TEXT_PAYLOAD",
                reason_code="EMPTY_OR_CORRUPT_FILE",
            )
        mime = "text/markdown" if ext == ".md" else "text/plain"
        return _ContainerCheckResult(is_clean=True, verified_mime_type=mime)

    async def scan_bytes(
        self,
        *,
        tenant_id: TenantId,
        filename: str,
        payload: bytes,
        expected_sha256: SHA256Hash,
    ) -> VirusScanOutcomeDTO:
        """Scan binary payload against size limits, checksums, malware, and magic bytes."""
        _require_tenant(tenant_id)
        actual_sha256 = SHA256Hash.from_bytes(payload)

        if not payload:
            return VirusScanOutcomeDTO(
                is_clean=False,
                detected_signature="EMPTY_PAYLOAD",
                verified_mime_type="application/octet-stream",
                verified_sha256=actual_sha256,
                reason_code="EMPTY_OR_CORRUPT_FILE",
            )

        if len(payload) > self._max_file_size_bytes:
            return VirusScanOutcomeDTO(
                is_clean=False,
                detected_signature="SIZE_LIMIT_EXCEEDED",
                verified_mime_type="application/octet-stream",
                verified_sha256=actual_sha256,
                reason_code="PAYLOAD_TOO_LARGE",
            )

        matched_malware = self._inspect_malware_signatures(payload)
        if matched_malware is not None:
            return VirusScanOutcomeDTO(
                is_clean=False,
                detected_signature=matched_malware,
                verified_mime_type="application/octet-stream",
                verified_sha256=actual_sha256,
                reason_code="MALWARE_DETECTED",
            )

        if actual_sha256 != expected_sha256:
            return VirusScanOutcomeDTO(
                is_clean=False,
                detected_signature="SHA256_MISMATCH",
                verified_mime_type="application/octet-stream",
                verified_sha256=actual_sha256,
                reason_code="CHECKSUM_MISMATCH",
            )

        ext = _extract_extension(filename)
        if ext not in _ALLOWED_EXTENSIONS:
            return VirusScanOutcomeDTO(
                is_clean=False,
                detected_signature=f"DISALLOWED_EXT:{ext or 'none'}",
                verified_mime_type="application/octet-stream",
                verified_sha256=actual_sha256,
                reason_code="UNSUPPORTED_FILE_TYPE",
            )

        if ext == ".pdf":
            result = self._inspect_pdf(payload)
        elif ext == ".docx":
            result = self._inspect_docx(payload)
        else:
            result = self._inspect_plain_text(payload, ext)

        return VirusScanOutcomeDTO(
            is_clean=result.is_clean,
            detected_signature=result.detected_signature,
            verified_mime_type=result.verified_mime_type,
            verified_sha256=actual_sha256,
            reason_code=result.reason_code,
        )
