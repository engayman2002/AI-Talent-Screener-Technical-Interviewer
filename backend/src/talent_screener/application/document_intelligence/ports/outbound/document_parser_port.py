"""Outbound Application Port for multi-format CV parsing and PII-safe extraction."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from talent_screener.application.document_intelligence.dtos import (
    DocumentExtractionCommandDTO,
    ExtractedCandidateDossierDTO,
)


@runtime_checkable
class DocumentParserPort(Protocol):
    """Port converting binary PDF/DOCX streams into a zero-PII ExtractedCandidateDossierDTO."""

    async def parse_and_extract(
        self,
        command: DocumentExtractionCommandDTO,
    ) -> ExtractedCandidateDossierDTO: ...
