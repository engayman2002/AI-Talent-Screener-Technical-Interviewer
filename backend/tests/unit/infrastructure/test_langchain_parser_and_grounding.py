"""Unit tests for Phase 3 Document Normalizer, Grounding Verifier & LangChain ACL."""

from __future__ import annotations

import asyncio
import io
import zipfile
import zlib
from collections.abc import Coroutine, Mapping
from typing import Any
from xml.sax.saxutils import escape

import pytest

from talent_screener.application.common.exceptions import ExternalAdapterFailureError
from talent_screener.application.document_intelligence.dtos import (
    DocumentExtractionCommandDTO,
)
from talent_screener.domain.contexts.document_intelligence import (
    ExtractedSkillClaim,
    ImpactMetric,
)
from talent_screener.domain.shared import ConfidenceScore, SHA256Hash, TenantId, generate_uuid7
from talent_screener.infrastructure.ai_adapters.langchain_parser import (
    DocumentSectionType,
    DocumentTextNormalizer,
    GroundingVerifier,
    LangChainDocumentParserAdapter,
    RawProjectPayload,
    RawSkillClaimPayload,
    RawStructuredDossierPayload,
    RawWorkExperiencePayload,
)
from talent_screener.infrastructure.persistence import TenantScopedObjectStorageAdapter
from talent_screener.infrastructure.security import CryptographicPiiVaultAdapter


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _make_flate_pdf(lines: tuple[str, ...]) -> bytes:
    bt_body = "\n".join(f"BT ({line}) Tj ET" for line in lines).encode("utf-8")
    compressed = zlib.compress(bt_body)
    return (
        b"%PDF-1.7\n1 0 obj << /Length "
        + str(len(compressed)).encode("ascii")
        + b" /Filter /FlateDecode >>\nstream\n"
        + compressed
        + b"\nendstream\nendobj\n%%EOF"
    )


def _make_docx(
    paragraphs: tuple[str, ...],
    *,
    inject_doctype: bool = False,
    header_text: str | None = None,
) -> bytes:
    buf = io.BytesIO()
    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    prefix = (
        '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe "pwn">]>'
        if inject_doctype
        else '<?xml version="1.0" encoding="UTF-8"?>'
    )
    doc_xml = (
        f'{prefix}<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", doc_xml)
        if header_text is not None:
            hdr_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:p><w:r><w:t>{escape(header_text)}</w:t><w:tab/><w:br/></w:r></w:p>"
                "</w:hdr>"
            ).encode()
            zf.writestr("word/header1.xml", hdr_xml)
    return buf.getvalue()


@pytest.mark.unit
def test_document_normalizer_extracts_pdf_docx_markdown_and_strips_injection() -> None:
    normalizer = DocumentTextNormalizer()

    # 1. Compressed FlateDecode PDF with invisible zero-width prompt injection characters
    pdf_bytes = _make_flate_pdf(
        (
            "SUMMARY",
            "Senior\u200b \u202eDistributed Systems Engineer.",
            "EXPERIENCE",
            "Lead Backend Engineer 2021-01 to 2024-12",
            "Built Python asyncio microservices handling 25k req/sec with PostgreSQL.",
            "SKILLS",
            "Python, FastAPI, Kubernetes",
        )
    )
    pdf_tree = normalizer.normalize_bytes(payload=pdf_bytes, mime_type="application/pdf")
    assert pdf_tree.stripped_invisible_chars == 2
    assert "\u200b" not in pdf_tree.full_text
    assert "\u202e" not in pdf_tree.full_text
    sec_types = {s.section_type for s in pdf_tree.sections}
    assert DocumentSectionType.SUMMARY in sec_types
    assert DocumentSectionType.EXPERIENCE in sec_types
    assert DocumentSectionType.SKILLS in sec_types
    assert all(s.start_line >= 1 and s.end_line >= s.start_line for s in pdf_tree.sections)

    # 2. Encrypted PDF raises permanent ENCRYPTED_DOCUMENT
    with pytest.raises(ExternalAdapterFailureError) as enc_exc:
        normalizer.normalize_bytes(
            payload=b"%PDF-1.7\n1 0 obj << /Encrypt 2 0 R >> endobj",
            mime_type="application/pdf",
        )
    assert enc_exc.value.code == "ENCRYPTED_DOCUMENT"
    assert not enc_exc.value.retryable

    # 3. DOCX container extraction (including header/tabs) & XXE/DTD hardening check
    docx_bytes = _make_docx(
        (
            "EXPERIENCE",
            "Staff AI Engineer 2022-01 to Present",
            "Architected LangChain extraction pipelines using Qdrant hybrid search.",
            "PROJECTS",
            "Built Rust & FastAPI hybrid index.",
            "CERTIFICATIONS",
            "CKA Certified Kubernetes Administrator",
        ),
        header_text="Curriculum Vitae",
    )
    docx_tree = normalizer.normalize_bytes(
        payload=docx_bytes,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "LangChain extraction pipelines" in docx_tree.full_text
    assert "Curriculum Vitae" in docx_tree.full_text
    docx_sec_types = {s.section_type for s in docx_tree.sections}
    assert DocumentSectionType.EXPERIENCE in docx_sec_types
    assert DocumentSectionType.PROJECTS in docx_sec_types
    assert DocumentSectionType.CERTIFICATIONS in docx_sec_types

    # UTF-16BE hex string PDF stream extraction & invalid PDF/XML errors
    hex_pdf = (
        b"%PDF-1.7\n1 0 obj << >>\nstream\n"
        b"BT <FEFF0050007900740068006F006E> Tj ET\nendstream\n%%EOF"
    )
    hex_tree = normalizer.normalize_bytes(payload=hex_pdf, mime_type="application/pdf")
    assert "Python" in hex_tree.full_text

    with pytest.raises(ExternalAdapterFailureError):
        normalizer.normalize_bytes(payload=b"", mime_type="text/plain")
    with pytest.raises(ExternalAdapterFailureError):
        normalizer.normalize_bytes(payload=b"\xff\xfe\xfd", mime_type="text/plain")
    with pytest.raises(ExternalAdapterFailureError):
        normalizer.normalize_bytes(payload=b"hello", mime_type="application/zip")

    xxe_docx = _make_docx(("Hello",), inject_doctype=True)
    with pytest.raises(ExternalAdapterFailureError) as xxe_exc:
        normalizer.normalize_bytes(
            payload=xxe_docx,
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert xxe_exc.value.code == "UNSAFE_XML_DTD_DETECTED"
    assert not xxe_exc.value.retryable

    # 4. NFKC normalization on Markdown text
    md_tree = normalizer.normalize_bytes(
        payload="\uff21\uff29 Engineer\nEDUCATION\nM.Sc. Computer Science".encode("utf-8"),
        mime_type="text/markdown",
    )
    assert "AI Engineer" in md_tree.full_text
    assert any(s.section_type is DocumentSectionType.EDUCATION for s in md_tree.sections)


@pytest.mark.unit
def test_grounding_verifier_accepts_verbatim_and_fuzzy_and_rejects_hallucinations() -> None:
    verifier = GroundingVerifier(grounding_threshold=0.85)
    source = (
        "EXPERIENCE\n"
        "Senior Backend Engineer 2021-01 to 2024-12\n"
        "Built Python asyncio microservices handling 25k requests/sec with PostgreSQL.\n"
    )
    claims = (
        # Exact case-insensitive substring match
        ExtractedSkillClaim(
            canonical_skill_id="SKILL_LANG_PYTHON",
            raw_label="Python",
            source_quote="built python asyncio microservices handling 25k requests/sec",
            claimed_depth=4,
            applied_months=48,
            recency_months=0,
            confidence=ConfidenceScore(0.95),
        ),
        # Fuzzy near-verbatim match (>= 0.85 similarity) with slightly different spacing/wording
        ExtractedSkillClaim(
            canonical_skill_id="SKILL_POSTGRESQL",
            raw_label="PostgreSQL",
            source_quote="Python asyncio microservices handling 25k requests/sec on PostgreSQL",
            claimed_depth=3,
            applied_months=36,
            recency_months=0,
            confidence=ConfidenceScore(0.90),
        ),
        # Hallucinated skill completely absent from CV
        ExtractedSkillClaim(
            canonical_skill_id="SKILL_KUBERNETES",
            raw_label="Kubernetes",
            source_quote="Deployed Kubernetes clusters with Istio service mesh across 50 regions",
            claimed_depth=4,
            applied_months=24,
            recency_months=0,
            confidence=ConfidenceScore(0.95),
        ),
    )

    accepted, rejected = verifier.verify_skill_claims(
        claims=claims,
        normalized_source_text=source,
    )
    assert [c.canonical_skill_id for c in accepted] == [
        "SKILL_LANG_PYTHON",
        "SKILL_POSTGRESQL",
    ]
    assert [r.identifier for r in rejected] == ["SKILL_KUBERNETES"]
    assert rejected[0].similarity_score < 0.85

    # Chronological validation & interval union deduplication (overlapping roles merged)
    r1 = verifier.validate_chronological_range(
        start_year=2021, start_month=1, end_year=2023, end_month=12, is_current=False
    )
    r2 = verifier.validate_chronological_range(
        start_year=2022, start_month=1, end_year=2024, end_month=12, is_current=False
    )
    bad = verifier.validate_chronological_range(
        start_year=2024, start_month=1, end_year=2022, end_month=6, is_current=False
    )
    current_range = verifier.validate_chronological_range(
        start_year=2023, start_month=6, end_year=None, end_month=None, is_current=True
    )
    assert bad is None
    assert r1 is not None and r2 is not None and current_range is not None

    # Impact metric verification strips fabricated metrics
    metrics_ok, metrics_rej = verifier.verify_impact_metrics(
        metrics=(
            ImpactMetric("LATENCY", 25.0, "K_RPS", "handling 25k requests/sec"),
            ImpactMetric("COST_SAVED", 99.0, "PERCENT", "saved 99% of AWS budget on mainframe"),
        ),
        normalized_source_text=source,
    )
    assert len(metrics_ok) == 1 and len(metrics_rej) == 1


@pytest.mark.unit
def test_langchain_parser_acl_strips_pii_before_llm_and_filters_hallucinations() -> None:
    class _SpyHallucinatingLlmClient:
        def __init__(self) -> None:
            self.captured_prompts: list[Mapping[str, object]] = []

        async def ainvoke(
            self, prompt_input: Mapping[str, object]
        ) -> RawStructuredDossierPayload:
            self.captured_prompts.append(prompt_input)
            return RawStructuredDossierPayload(
                work_experiences=(
                    RawWorkExperiencePayload(
                        role_title="Senior Distributed Systems Engineer",
                        normalized_role="BACKEND_ENGINEER",
                        company_token="COMPANY_1",
                        start_year=2021,
                        start_month=1,
                        end_year=2024,
                        end_month=12,
                    ),
                    # Reversed date should be stripped safely
                    RawWorkExperiencePayload(
                        role_title="Invalid Reversed Role",
                        normalized_role="OTHER",
                        company_token="BAD",
                        start_year=2024,
                        start_month=6,
                        end_year=2022,
                        end_month=1,
                    ),
                ),
                projects=(
                    RawProjectPayload(
                        project_name="Async Ingestion Engine",
                        scale_tier="HIGH_THROUGHPUT_PRODUCTION",
                        summary="Built Python asyncio microservices handling 25k requests/sec.",
                        source_quote="Built Python asyncio microservices handling 25k requests/sec",
                    ),
                    RawProjectPayload(
                        project_name="Hallucinated Blockchain Core",
                        scale_tier="HIGH_THROUGHPUT_PRODUCTION",
                        summary="Invented fake Solana smart contract consensus layer.",
                        source_quote="Authored Rust Solana smart contracts in zero-knowledge VM",
                    ),
                ),
                skill_claims=(
                    RawSkillClaimPayload(
                        canonical_skill_id="SKILL_LANG_PYTHON",
                        raw_label="Python",
                        source_quote="Built Python asyncio microservices handling 25k requests/sec",
                        claimed_depth=4,
                        applied_months=48,
                        recency_months=0,
                    ),
                    RawSkillClaimPayload(
                        canonical_skill_id="SKILL_BLOCKCHAIN_FAKE",
                        raw_label="Solidity",
                        source_quote="Implemented ERC-20 Solidity contracts on Ethereum mainnet",
                        claimed_depth=4,
                        applied_months=24,
                        recency_months=0,
                    ),
                ),
                extraction_confidence=0.95,
            )

    async def _run_test() -> None:
        storage = TenantScopedObjectStorageAdapter()
        vault = CryptographicPiiVaultAdapter()
        spy_llm = _SpyHallucinatingLlmClient()
        adapter = LangChainDocumentParserAdapter(
            object_storage=storage,
            pii_vault=vault,
            llm_client=spy_llm,
        )
        tid = TenantId.generate()
        batch_id = generate_uuid7()
        task_id = generate_uuid7()
        cv_bytes = _make_flate_pdf(
            (
                "Name: Jane Doe",
                "Email: jane.doe@secretmail.org",
                "Phone: +1-555-222-3333",
                "EXPERIENCE",
                "Senior Distributed Systems Engineer 2021-01 to 2024-12",
                "Built Python asyncio microservices handling 25k requests/sec.",
            )
        )
        sha = await storage.put_bytes(
            tenant_id=tid,
            object_key=f"{tid.value}/{batch_id}/jane.pdf",
            payload=cv_bytes,
            content_type="application/pdf",
        )
        dossier = await adapter.parse_and_extract(
            DocumentExtractionCommandDTO(
                tenant_id=tid,
                batch_id=batch_id,
                batch_file_id=task_id,
                document_stream_uri=f"{tid.value}/{batch_id}/jane.pdf",
                mime_type="application/pdf",
                expected_sha256=sha,
            )
        )

        # Verify ZERO PII leaked into the LLM prompt input!
        prompt_text = str(spy_llm.captured_prompts[0]["delimited_prompt"])
        assert "jane.doe@secretmail.org" not in prompt_text
        assert "555-222-3333" not in prompt_text
        assert "Jane Doe" not in prompt_text
        assert "[PII_EMAIL_1]" in prompt_text

        # Verify hallucinated skills, projects, and bad date ranges were filtered out!
        assert [s.canonical_skill_id for s in dossier.skill_claims] == ["SKILL_LANG_PYTHON"]
        assert [p.project_name for p in dossier.projects] == ["Async Ingestion Engine"]
        assert len(dossier.work_experiences) == 1
        assert dossier.seniority_estimate.verified_experience_months == 48
        assert adapter.last_telemetry is not None
        assert len(adapter.last_telemetry.rejected_hallucinations) == 3

        # Verify transient TimeoutError becomes retryable ExternalAdapterFailureError
        class _TimeoutLlm:
            async def ainvoke(
                self, prompt_input: Mapping[str, object]
            ) -> RawStructuredDossierPayload:
                raise TimeoutError("LLM timeout")

        timeout_adapter = LangChainDocumentParserAdapter(
            object_storage=storage,
            pii_vault=vault,
            llm_client=_TimeoutLlm(),
        )
        with pytest.raises(ExternalAdapterFailureError) as exc_info:
            await timeout_adapter.parse_and_extract(
                DocumentExtractionCommandDTO(
                    tenant_id=tid,
                    batch_id=batch_id,
                    batch_file_id=task_id,
                    document_stream_uri=f"{tid.value}/{batch_id}/jane.pdf",
                    mime_type="application/pdf",
                    expected_sha256=sha,
                )
            )
        assert exc_info.value.retryable

        # Verify Pydantic .model_dump()-style structured output & all-hallucinated error
        class _ModelDumpObj:
            def model_dump(self) -> dict[str, object]:
                return {
                    "work_experiences": [],
                    "projects": [],
                    "skill_claims": [
                        {
                            "canonical_skill_id": "SKILL_JAVA",
                            "raw_label": "Java",
                            "source_quote": "Fabricated Java EE monolith claim",
                        }
                    ],
                }

        class _DictModelLlm:
            async def ainvoke(self, prompt_input: Mapping[str, object]) -> object:
                return _ModelDumpObj()

        hallucinated_adapter = LangChainDocumentParserAdapter(
            object_storage=storage,
            pii_vault=vault,
            llm_client=_DictModelLlm(),  # type: ignore[arg-type]
        )
        with pytest.raises(ExternalAdapterFailureError) as all_bad:
            await hallucinated_adapter.parse_and_extract(
                DocumentExtractionCommandDTO(
                    tenant_id=tid,
                    batch_id=batch_id,
                    batch_file_id=task_id,
                    document_stream_uri=f"{tid.value}/{batch_id}/jane.pdf",
                    mime_type="application/pdf",
                    expected_sha256=sha,
                )
            )
        assert all_bad.value.code == "NO_GROUNDED_SKILLS_EXTRACTED"
        assert not all_bad.value.retryable

        # Checksum mismatch raises permanent CHECKSUM_MISMATCH
        with pytest.raises(ExternalAdapterFailureError) as sha_bad:
            await adapter.parse_and_extract(
                DocumentExtractionCommandDTO(
                    tenant_id=tid,
                    batch_id=batch_id,
                    batch_file_id=task_id,
                    document_stream_uri=f"{tid.value}/{batch_id}/jane.pdf",
                    mime_type="application/pdf",
                    expected_sha256=SHA256Hash.from_text("mismatch"),
                )
            )
        assert sha_bad.value.code == "CHECKSUM_MISMATCH"

    _run(_run_test())
