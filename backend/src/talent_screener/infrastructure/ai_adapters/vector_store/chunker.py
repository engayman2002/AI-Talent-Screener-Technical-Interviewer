"""ADR-004 Hierarchical Parent-Child Semantic Chunker for Candidate Dossiers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from uuid import UUID

from talent_screener.application.document_intelligence.dtos import (
    ExtractedCandidateDossierDTO,
)
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.ai_adapters.langchain_parser.normalizer import (
    DocumentSectionType,
    DocumentTextNormalizer,
    NormalizedSection,
)

# Deterministic namespace UUIDs for RFC 4122 UUIDv5 idempotent point identity (ADR-004).
_PARENT_CHUNK_NS = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
_CHILD_CHUNK_NS = UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")

_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_WORD_WS_RE = re.compile(r"\S+")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _estimate_token_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _WORD_WS_RE.finditer(text)]


@dataclass(frozen=True, slots=True)
class ParentChunk:
    """Section-level parent container preserving full contextual narrative."""

    parent_chunk_id: UUID
    tenant_id: TenantId
    candidate_id: UUID
    requisition_id: UUID
    section_type: DocumentSectionType
    heading: str
    content: str
    content_hash: str
    skill_tags: tuple[str, ...]
    last_used_year: int | None
    sync_version: int


@dataclass(frozen=True, slots=True)
class ChildChunk:
    """Granular overlapping child passage linked to its ParentChunk via UUIDv5."""

    chunk_id: UUID
    parent_chunk_id: UUID
    tenant_id: TenantId
    candidate_id: UUID
    requisition_id: UUID
    section_type: DocumentSectionType
    chunk_index: int
    content_hash: str
    contextualized_text: str
    raw_excerpt: str
    token_count: int
    skill_tags: tuple[str, ...]
    last_used_year: int | None
    recency_months_delta: int
    evidentiary_depth: int
    sync_version: int


@dataclass(frozen=True, slots=True)
class HierarchicalChunkBundle:
    """Resulting Parent + Child chunk hierarchy emitted for dual vector indexing."""

    parent_chunks: tuple[ParentChunk, ...]
    child_chunks: tuple[ChildChunk, ...]


class HierarchicalDocumentChunker:
    """Segments normalized candidate dossiers into ADR-004 Parent-Child chunks."""

    def __init__(
        self,
        *,
        child_min_tokens: int = 250,
        child_target_tokens: int = 320,
        child_max_tokens: int = 400,
        child_overlap_tokens: int = 50,
        normalizer: DocumentTextNormalizer | None = None,
    ) -> None:
        if child_min_tokens < 1 or child_target_tokens < child_min_tokens:
            raise ValueError("Invalid child chunk token bounds.")
        if child_max_tokens < child_target_tokens:
            raise ValueError("child_max_tokens must be >= child_target_tokens.")
        if child_overlap_tokens < 0 or child_overlap_tokens >= child_target_tokens:
            raise ValueError("child_overlap_tokens must satisfy 0 <= overlap < target.")
        self._min_tokens = child_min_tokens
        self._target_tokens = child_target_tokens
        self._max_tokens = child_max_tokens
        self._overlap_tokens = child_overlap_tokens
        self._normalizer = normalizer if normalizer is not None else DocumentTextNormalizer()

    @staticmethod
    def deterministic_parent_id(
        *,
        tenant_id: TenantId,
        candidate_id: UUID,
        section_type: DocumentSectionType,
        section_index: int,
        content_hash: str,
    ) -> UUID:
        """Compute reproducible UUIDv5 parent chunk ID."""
        seed = (
            f"{tenant_id.value}:{candidate_id}:{section_type.value}:"
            f"{section_index}:{content_hash}"
        )
        return uuid5_from_seed(_PARENT_CHUNK_NS, seed)

    @staticmethod
    def deterministic_child_id(
        *,
        tenant_id: TenantId,
        candidate_id: UUID,
        section_type: DocumentSectionType,
        chunk_index: int,
        content_hash: str,
    ) -> UUID:
        """Compute reproducible UUIDv5 child chunk ID from canonical tuple."""
        seed = (
            f"{tenant_id.value}:{candidate_id}:{section_type.value}:"
            f"{chunk_index}:{content_hash}"
        )
        return uuid5_from_seed(_CHILD_CHUNK_NS, seed)

    def _split_into_windows(self, content: str) -> list[tuple[str, int]]:
        spans = _estimate_token_spans(content)
        total_tokens = len(spans)
        if total_tokens == 0:
            return []
        if total_tokens <= self._max_tokens:
            return [(content.strip(), total_tokens)]

        windows: list[tuple[str, int]] = []
        stride = max(1, self._target_tokens - self._overlap_tokens)
        start_tok = 0
        while start_tok < total_tokens:
            end_tok = min(total_tokens, start_tok + self._target_tokens)
            # Merge a tiny trailing tail into the final chunk if within max_tokens.
            if (
                total_tokens - end_tok > 0
                and end_tok - start_tok + (total_tokens - end_tok) <= self._max_tokens
                and (total_tokens - end_tok) < (self._min_tokens // 2)
            ):
                end_tok = total_tokens
            char_start = spans[start_tok][0]
            char_end = spans[end_tok - 1][1]
            excerpt = content[char_start:char_end].strip()
            if excerpt:
                windows.append((excerpt, end_tok - start_tok))
            if end_tok >= total_tokens:
                break
            start_tok += stride
        return windows

    def _infer_chunk_metadata(
        self,
        *,
        section_type: DocumentSectionType,
        chunk_text: str,
        dossier: ExtractedCandidateDossierDTO,
    ) -> tuple[tuple[str, ...], int | None, int, int]:
        lower_chunk = chunk_text.lower()
        matched_tags: list[str] = []
        matched_depths: list[int] = []
        matched_recencies: list[int] = []

        for claim in dossier.skill_claims:
            label_match = claim.raw_label.lower() in lower_chunk
            id_tail = claim.canonical_skill_id.lower().removeprefix("skill_").replace("_", " ")
            quote_match = claim.source_quote.lower() in lower_chunk
            if label_match or (len(id_tail) >= 2 and id_tail in lower_chunk) or quote_match:
                if claim.canonical_skill_id not in matched_tags:
                    matched_tags.append(claim.canonical_skill_id)
                matched_depths.append(claim.claimed_depth)
                matched_recencies.append(claim.recency_months)

        if not matched_tags and section_type in {
            DocumentSectionType.EXPERIENCE,
            DocumentSectionType.PROJECTS,
        }:
            # Inherit primary claims if the role section describes overarching work.
            for claim in dossier.skill_claims[:3]:
                if claim.canonical_skill_id not in matched_tags:
                    matched_tags.append(claim.canonical_skill_id)

        years = [int(y) for y in _YEAR_RE.findall(chunk_text)]
        last_used_year: int | None = max(years) if years else None
        if last_used_year is None and dossier.work_experiences:
            if any(w.is_current for w in dossier.work_experiences):
                last_used_year = None
            else:
                end_years = [w.end_year for w in dossier.work_experiences if w.end_year is not None]
                last_used_year = max(end_years) if end_years else None

        if matched_recencies:
            recency_months = min(matched_recencies)
        elif any(w.is_current for w in dossier.work_experiences):
            recency_months = 0
        elif dossier.skill_claims:
            recency_months = min(c.recency_months for c in dossier.skill_claims)
        else:
            recency_months = 0

        if section_type is DocumentSectionType.EXPERIENCE:
            base_depth = max(matched_depths) if matched_depths else 4
            depth = max(3, min(4, base_depth))
        elif section_type is DocumentSectionType.PROJECTS:
            base_depth = max(matched_depths) if matched_depths else 3
            depth = max(3, min(4, base_depth))
        elif section_type is DocumentSectionType.SKILLS:
            depth = 1
        elif section_type in {DocumentSectionType.SUMMARY, DocumentSectionType.EDUCATION}:
            depth = 2
        else:
            depth = max(1, min(4, max(matched_depths) if matched_depths else 2))

        return tuple(matched_tags), last_used_year, max(0, min(720, recency_months)), depth

    def chunk_candidate_dossier(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_id: UUID,
        dossier: ExtractedCandidateDossierDTO,
        sync_version: int = 1,
    ) -> HierarchicalChunkBundle:
        """Build deterministic Parent and overlapping Child chunks from a dossier."""
        tree = self._normalizer.normalize_text(raw_text=dossier.raw_redacted_text)
        sections: list[NormalizedSection] = list(tree.sections)

        # Include explicit synthesized project/experience supplement only if raw sections omit them.
        if not sections:
            sections = [
                NormalizedSection(
                    section_type=DocumentSectionType.OTHER,
                    heading="DOSSIER",
                    content=dossier.raw_redacted_text,
                    start_line=1,
                    end_line=1,
                    start_offset=0,
                    end_offset=len(dossier.raw_redacted_text),
                )
            ]

        parents: list[ParentChunk] = []
        children: list[ChildChunk] = []
        seniority_badge = dossier.seniority_estimate.level
        tenure_months = dossier.seniority_estimate.verified_experience_months

        for sec_idx, sec in enumerate(sections):
            parent_hash = _sha256_hex(f"{sec.section_type.value}:{sec.content}")
            parent_id = self.deterministic_parent_id(
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                section_type=sec.section_type,
                section_index=sec_idx,
                content_hash=parent_hash,
            )
            p_tags, p_year, _, _ = self._infer_chunk_metadata(
                section_type=sec.section_type,
                chunk_text=sec.content,
                dossier=dossier,
            )
            parents.append(
                ParentChunk(
                    parent_chunk_id=parent_id,
                    tenant_id=tenant_id,
                    candidate_id=candidate_id,
                    requisition_id=requisition_id,
                    section_type=sec.section_type,
                    heading=sec.heading,
                    content=sec.content,
                    content_hash=parent_hash,
                    skill_tags=p_tags,
                    last_used_year=p_year,
                    sync_version=sync_version,
                )
            )

            windows = self._split_into_windows(sec.content)
            for chunk_idx, (excerpt, tok_count) in enumerate(windows):
                tags, last_year, recency_months, depth = self._infer_chunk_metadata(
                    section_type=sec.section_type,
                    chunk_text=excerpt,
                    dossier=dossier,
                )
                header = (
                    f"[Section: {sec.section_type.value} | Seniority: {seniority_badge} | "
                    f"Tenure: {tenure_months}m | Depth: {depth}]"
                )
                contextualized = f"{header} {excerpt}"
                c_hash = _sha256_hex(contextualized)
                child_id = self.deterministic_child_id(
                    tenant_id=tenant_id,
                    candidate_id=candidate_id,
                    section_type=sec.section_type,
                    chunk_index=chunk_idx,
                    content_hash=c_hash,
                )
                children.append(
                    ChildChunk(
                        chunk_id=child_id,
                        parent_chunk_id=parent_id,
                        tenant_id=tenant_id,
                        candidate_id=candidate_id,
                        requisition_id=requisition_id,
                        section_type=sec.section_type,
                        chunk_index=chunk_idx,
                        content_hash=c_hash,
                        contextualized_text=contextualized,
                        raw_excerpt=excerpt[:480],
                        token_count=tok_count,
                        skill_tags=tags,
                        last_used_year=last_year,
                        recency_months_delta=recency_months,
                        evidentiary_depth=depth,
                        sync_version=sync_version,
                    )
                )

        return HierarchicalChunkBundle(
            parent_chunks=tuple(parents),
            child_chunks=tuple(children),
        )


def uuid5_from_seed(namespace: UUID, seed: str) -> UUID:
    """Typed wrapper over uuid.uuid5 ensuring consistent deterministic RFC 4122 IDs."""
    import uuid

    return uuid.uuid5(namespace, seed)
