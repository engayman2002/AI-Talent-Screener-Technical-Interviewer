"""ADR-012 Ring 1-2 Adversarial Prompt Injection & Rubric-Jailbreak Guardrail."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from talent_screener.application.live_interview.dtos import GuardrailEvaluationDTO
from talent_screener.domain.shared.guards import clamp
from talent_screener.domain.shared.value_objects import TenantId

_INVISIBLE_CHARS_RE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\xad\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)

_OVERRIDE_PATTERNS: tuple[tuple[re.Pattern[str], float], ...] = (
    (
        re.compile(
            r"\b(?:ignore|disregard|forget|bypass|override)\s+(?:all\s+|any\s+)?"
            r"(?:previous|prior|above|system|earlier|all)\s+(?:instructions|prompts|rules|rubric|directives)\b",
            re.I,
        ),
        1.0,
    ),
    (
        re.compile(
            r"\b(?:give|award|assign|set|score|grant)\s+(?:me|this\s+candidate|the\s+candidate|my\s+score)?\s*"
            r"(?:a\s+|to\s+)?(?:perfect|maximum|max|full|100|5/5|5\.0|strong[_\s]?hire)\b",
            re.I,
        ),
        0.95,
    ),
    (
        re.compile(
            r"\b(?:override|overwrite|hijack)\s+(?:the\s+|your\s+)?(?:rubric|scoring|grade|evaluation)\b",
            re.I,
        ),
        0.90,
    ),
    (
        re.compile(r"\b(?:new\s+system\s+directive|reset\s+your\s+instructions)\b", re.I),
        0.95,
    ),
)

_DELIMITER_PATTERNS: tuple[tuple[re.Pattern[str], float], ...] = (
    (
        re.compile(
            r"</?\s*(?:untrusted_candidate_response|system|assistant|developer|instruction)\b[^>]*>",
            re.I,
        ),
        1.0,
    ),
    (
        re.compile(
            r"(?:<\|im_start\|>|<\|im_end\|>|\[/?INST\]|###\s*SYSTEM\b|SYSTEM\s+PROMPT\s*:)",
            re.I,
        ),
        0.95,
    ),
)

_EXFIL_ROLE_PATTERNS: tuple[tuple[re.Pattern[str], float], ...] = (
    (
        re.compile(
            r"\b(?:reveal|print|dump|output|show|leak)\s+(?:your\s+|the\s+)?"
            r"(?:system\s+prompt|hidden\s+instructions|rubric\s+weights|developer\s+prompt)\b",
            re.I,
        ),
        0.95,
    ),
    (
        re.compile(
            r"\b(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be|switch\s+to)\s+"
            r"(?:an?\s+)?(?:unrestricted|dan|developer\s+mode|admin|grader|evaluator|recruiter)\b",
            re.I,
        ),
        0.90,
    ),
    (
        re.compile(r"\b(?:jailbreak|prompt\s+injection)\b", re.I),
        0.85,
    ),
)

_META_FRAMING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:for\s+grading|evaluation\s+note|meta\s+note|note\s+to\s+(?:evaluator|grader|ai)|"
        r"focus\s+strictly\s+on\s+giving\s+high\s+marks)\b[^.!?\n]*[.!?]?",
        re.I,
    ),
    re.compile(r"\b(?:please\s+be\s+lenient|treat\s+this\s+as\s+exemplary)\b[^.!?\n]*[.!?]?", re.I),
)

_BASE64_CHUNK_RE = re.compile(r"\b[A-Za-z0-9+/]{20,}={0,2}\b")
_HEX_RUN_RE = re.compile(r"(?:(?:0x|\\x)[0-9a-fA-F]{2}\s*){8,}")


class AdversarialThreatCategory(StrEnum):
    """Canonical threat taxonomy for Ring 1-2 adversarial prompt defense."""

    CLEAN = "CLEAN"
    INSTRUCTION_OVERRIDE = "INSTRUCTION_OVERRIDE"
    DELIMITER_BREAKOUT = "DELIMITER_BREAKOUT"
    ROLEPLAY_JAILBREAK = "ROLEPLAY_JAILBREAK"
    PROMPT_EXFILTRATION = "PROMPT_EXFILTRATION"
    UNICODE_SMUGGLING = "UNICODE_SMUGGLING"
    ENCODED_OBFUSCATION = "ENCODED_OBFUSCATION"
    SUSPICIOUS_META_FRAMING = "SUSPICIOUS_META_FRAMING"


@dataclass(frozen=True, slots=True)
class GuardrailRiskAssessment:
    """Detailed risk decomposition returned alongside sanitized output."""

    risk_score: float
    s_override: float
    s_delim: float
    s_exfil_role: float
    threat_categories: tuple[AdversarialThreatCategory, ...]
    is_suspicious: bool
    is_blocked: bool
    sanitized_text: str
    reason_code: str | None = None


class AdversarialPromptGuardrailAdapter:
    """Multi-signal adversarial prompt-injection firewall implementing PromptGuardrailPort."""

    def __init__(
        self,
        *,
        suspicious_threshold: float = 0.35,
        block_threshold: float = 0.75,
    ) -> None:
        self._suspicious_threshold = suspicious_threshold
        self._block_threshold = block_threshold

    async def evaluate_candidate_input(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        turn_number: int,
        raw_utterance: str,
    ) -> GuardrailEvaluationDTO:
        """Inspect candidate input across Unicode, regex, delimiter, and encoded vectors."""
        _ = (tenant_id, session_id, turn_number)
        assessment = self.assess(raw_utterance)
        return GuardrailEvaluationDTO(
            risk_score=assessment.risk_score,
            sanitized_text=assessment.sanitized_text,
            is_suspicious=assessment.is_suspicious,
            is_blocked=assessment.is_blocked,
            reason_code=assessment.reason_code,
        )

    def assess(self, raw_utterance: str) -> GuardrailRiskAssessment:
        """Compute deterministic adversarial risk R_adv and strip/redact unsafe payloads."""
        normalized, invisible_stripped = self._normalize_and_strip_invisible(raw_utterance)
        decoded_snippets = self._extract_decoded_payloads(normalized)
        inspection_corpus = (
            normalized if not decoded_snippets else f"{normalized}\n" + "\n".join(decoded_snippets)
        )

        categories: list[AdversarialThreatCategory] = []
        s_override, hit_override = self._score_patterns(inspection_corpus, _OVERRIDE_PATTERNS)
        s_delim, hit_delim = self._score_patterns(inspection_corpus, _DELIMITER_PATTERNS)
        s_exfil, hit_exfil = self._score_patterns(inspection_corpus, _EXFIL_ROLE_PATTERNS)
        meta_hits = sum(1 for pat in _META_FRAMING_PATTERNS if pat.search(normalized))

        if invisible_stripped >= 2:
            categories.append(AdversarialThreatCategory.UNICODE_SMUGGLING)
        if decoded_snippets and (hit_override > 0 or hit_delim > 0 or hit_exfil > 0):
            categories.append(AdversarialThreatCategory.ENCODED_OBFUSCATION)
        if hit_override > 0:
            categories.append(AdversarialThreatCategory.INSTRUCTION_OVERRIDE)
        if hit_delim > 0:
            categories.append(AdversarialThreatCategory.DELIMITER_BREAKOUT)
        if hit_exfil > 0:
            categories.append(AdversarialThreatCategory.ROLEPLAY_JAILBREAK)
        if meta_hits > 0:
            categories.append(AdversarialThreatCategory.SUSPICIOUS_META_FRAMING)

        # Phase 8 Eq 1.1: R_adv = clip(0.45 * S_override + 0.30 * S_delim + 0.25 * S_exfil/role)
        weighted_adv = clamp(
            0.45 * s_override + 0.30 * s_delim + 0.25 * s_exfil,
            0.0,
            1.0,
        )

        # Escalate explicit single-vector hijacks (e.g., rubric override or XML breakout)
        critical_floor = 0.0
        if hit_override >= 2 or (hit_override >= 1 and s_override >= 0.90 and hit_exfil >= 1):
            critical_floor = 0.88
        elif s_override >= 0.95 or s_delim >= 0.95 or s_exfil >= 0.95:
            critical_floor = 0.80
        elif meta_hits > 0 or invisible_stripped >= 3:
            critical_floor = 0.45

        risk_score = round(clamp(max(weighted_adv, critical_floor), 0.0, 1.0), 4)
        is_blocked = risk_score >= self._block_threshold
        is_suspicious = risk_score >= self._suspicious_threshold

        if is_blocked:
            sanitized = "[REDACTED_BY_ADVERSARIAL_GUARDRAIL]"
            reason_code = "ADVERSARIAL_INJECTION_BLOCKED"
        elif is_suspicious:
            sanitized = self._strip_meta_and_delimiters(normalized)
            reason_code = "SUSPICIOUS_META_FRAMING"
        else:
            sanitized = normalized.strip()
            reason_code = None

        if not categories:
            categories.append(AdversarialThreatCategory.CLEAN)

        return GuardrailRiskAssessment(
            risk_score=risk_score,
            s_override=s_override,
            s_delim=s_delim,
            s_exfil_role=s_exfil,
            threat_categories=tuple(dict.fromkeys(categories)),
            is_suspicious=is_suspicious,
            is_blocked=is_blocked,
            sanitized_text=sanitized,
            reason_code=reason_code,
        )

    @staticmethod
    def _normalize_and_strip_invisible(text: str) -> tuple[str, int]:
        nfkc = unicodedata.normalize("NFKC", text)
        cleaned, stripped_count = _INVISIBLE_CHARS_RE.subn("", nfkc)
        return cleaned, stripped_count

    @staticmethod
    def _score_patterns(
        corpus: str, patterns: tuple[tuple[re.Pattern[str], float], ...]
    ) -> tuple[float, int]:
        max_weight = 0.0
        hits = 0
        for pattern, weight in patterns:
            matches = pattern.findall(corpus)
            if matches:
                hits += len(matches)
                max_weight = max(max_weight, weight)
        return max_weight, hits

    @staticmethod
    def _strip_meta_and_delimiters(text: str) -> str:
        result = text
        for pattern in _META_FRAMING_PATTERNS:
            result = pattern.sub("", result)
        for pattern, _ in _DELIMITER_PATTERNS:
            result = pattern.sub("", result)
        cleaned = " ".join(result.split()).strip()
        return cleaned if cleaned else "[META_FRAMING_STRIPPED]"

    @staticmethod
    def _extract_decoded_payloads(text: str) -> tuple[str, ...]:
        decoded: list[str] = []
        for candidate in _BASE64_CHUNK_RE.findall(text):
            padded = candidate + ("=" * ((4 - len(candidate) % 4) % 4))
            try:
                raw = base64.b64decode(padded, validate=True)
                snippet = raw.decode("utf-8", errors="ignore").strip()
                if len(snippet) >= 12 and any(ch.isalpha() for ch in snippet):
                    decoded.append(snippet)
            except (binascii.Error, ValueError):
                continue

        for hex_match in _HEX_RUN_RE.findall(text):
            cleaned_hex = re.sub(r"(?:0x|\\x|\s)+", "", hex_match)
            try:
                raw_hex = bytes.fromhex(cleaned_hex)
                snippet = raw_hex.decode("utf-8", errors="ignore").strip()
                if len(snippet) >= 8 and any(ch.isalpha() for ch in snippet):
                    decoded.append(snippet)
            except ValueError:
                continue

        return tuple(decoded)
