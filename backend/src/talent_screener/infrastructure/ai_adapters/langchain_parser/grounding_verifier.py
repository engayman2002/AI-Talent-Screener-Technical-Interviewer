"""Deterministic Anti-Hallucination Grounding Verifier & Date Normalizer."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from talent_screener.domain.contexts.document_intelligence.value_objects import (
    ExtractedSkillClaim,
    ImpactMetric,
)
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import ConfidenceScore

_WHITESPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#./_-]*", re.IGNORECASE)


def _collapse_ws(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.strip())


def _levenshtein_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    len_a, len_b = len(a), len(b)
    if len_a == 0 or len_b == 0:
        return 0.0
    if len_a < len_b:
        a, b = b, a
        len_a, len_b = len_b, len_a
    prev_row = list(range(len_b + 1))
    for i, ch_a in enumerate(a, start=1):
        curr_row = [i]
        for j, ch_b in enumerate(b, start=1):
            cost = 0 if ch_a == ch_b else 1
            curr_row.append(
                min(
                    curr_row[j - 1] + 1,
                    prev_row[j] + 1,
                    prev_row[j - 1] + cost,
                )
            )
        prev_row = curr_row
    distance = prev_row[len_b]
    return max(0.0, 1.0 - (distance / max(len_a, len_b)))


def _token_jaccard(tokens_a: Sequence[str], tokens_b: Sequence[str]) -> float:
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def compute_quote_grounding_score(
    quote: str,
    source_text: str,
) -> tuple[float, str | None]:
    """Compute exact-substring or sliding-window token/Levenshtein grounding score."""
    collapsed_quote = _collapse_ws(quote)
    collapsed_source = _collapse_ws(source_text)
    if len(collapsed_quote) < 3 or not collapsed_source:
        return 0.0, None

    lower_quote = collapsed_quote.lower()
    lower_source = collapsed_source.lower()
    exact_idx = lower_source.find(lower_quote)
    if exact_idx != -1:
        matched_span = collapsed_source[exact_idx : exact_idx + len(lower_quote)]
        return 1.0, matched_span

    quote_words = lower_quote.split(" ")
    source_orig_words = collapsed_source.split(" ")
    source_lower_words = [w.lower() for w in source_orig_words]
    if not quote_words or not source_lower_words:
        return 0.0, None

    # Pre-strip punctuation for token comparison while keeping original spans recoverable.
    q_norm_tokens = _WORD_RE.findall(lower_quote)
    if not q_norm_tokens:
        return 0.0, None

    q_len = len(quote_words)
    min_win = max(1, q_len - 2)
    max_win = min(len(source_lower_words), q_len + 3)

    best_score = 0.0
    best_span: str | None = None

    for start in range(len(source_lower_words)):
        for win_len in range(min_win, max_win + 1):
            end = start + win_len
            if end > len(source_lower_words):
                break
            win_str = " ".join(source_lower_words[start:end])
            win_norm_tokens = _WORD_RE.findall(win_str)
            jacc = _token_jaccard(q_norm_tokens, win_norm_tokens)
            lev = _levenshtein_similarity(lower_quote, win_str)
            score = max(jacc, lev)
            if score > best_score:
                best_score = score
                best_span = " ".join(source_orig_words[start:end])

    return best_score, best_span


@dataclass(frozen=True, slots=True)
class RejectedClaimRecord:
    """Telemetry record for an ungrounded hallucinated claim stripped by the ACL."""

    category: str
    identifier: str
    raw_quote: str
    similarity_score: float
    reason: str


@dataclass(frozen=True, slots=True)
class VerifiedSkillVerificationOutcome:
    """Verification outcome wrapping either an accepted claim or rejection telemetry."""

    accepted: bool
    similarity_score: float
    canonical_quote: str


@dataclass(frozen=True, slots=True)
class VerifiedWorkDateRange:
    """Validated chronological work experience dates and computed duration."""

    start_year: int
    start_month: int
    end_year: int | None
    end_month: int | None
    is_current: bool
    duration_months: int
    recency_months: int


def compute_union_experience_months(
    ranges: Sequence[VerifiedWorkDateRange],
    *,
    as_of: date | None = None,
) -> int:
    """Deterministically compute union of non-overlapping verified months in [0, 720]."""
    if not ranges:
        return 0
    ref = as_of if as_of is not None else utc_now().date()
    ref_end_idx = ref.year * 12 + (ref.month - 1) + 1
    intervals: list[tuple[int, int]] = []
    for rng in ranges:
        start_idx = rng.start_year * 12 + (rng.start_month - 1)
        if rng.is_current:
            end_idx = max(start_idx, ref_end_idx)
        else:
            assert rng.end_year is not None and rng.end_month is not None
            end_idx = rng.end_year * 12 + (rng.end_month - 1) + 1
        intervals.append((start_idx, end_idx))

    intervals.sort()
    merged: list[tuple[int, int]] = [intervals[0]]
    for start, end in intervals[1:]:
        p_start, p_end = merged[-1]
        if start <= p_end:
            merged[-1] = (p_start, max(p_end, end))
        else:
            merged.append((start, end))
    return min(720, sum(max(0, end - start) for start, end in merged))


class GroundingVerifier:
    """Anti-hallucination engine rejecting ungrounded LLM claims before Domain entry."""

    def __init__(self, *, grounding_threshold: float = 0.85) -> None:
        if not 0.5 <= grounding_threshold <= 1.0:
            raise ValueError("grounding_threshold must be within [0.5, 1.0].")
        self._threshold = grounding_threshold

    @property
    def grounding_threshold(self) -> float:
        return self._threshold

    def verify_quote(
        self,
        *,
        source_quote: str,
        normalized_source_text: str,
    ) -> VerifiedSkillVerificationOutcome:
        """Check whether a claim quote is verbatim or >= tau_grounding grounded in text."""
        score, matched_span = compute_quote_grounding_score(
            source_quote, normalized_source_text
        )
        if score < self._threshold or matched_span is None:
            return VerifiedSkillVerificationOutcome(
                accepted=False,
                similarity_score=score,
                canonical_quote=source_quote.strip(),
            )
        return VerifiedSkillVerificationOutcome(
            accepted=True,
            similarity_score=score,
            canonical_quote=matched_span[:480],
        )

    def verify_skill_claims(
        self,
        *,
        claims: Sequence[ExtractedSkillClaim],
        normalized_source_text: str,
    ) -> tuple[tuple[ExtractedSkillClaim, ...], tuple[RejectedClaimRecord, ...]]:
        """Filter skill claims against normalized text, snapping fuzzy quotes to source spans."""
        accepted: list[ExtractedSkillClaim] = []
        rejected: list[RejectedClaimRecord] = []

        for claim in claims:
            outcome = self.verify_quote(
                source_quote=claim.source_quote,
                normalized_source_text=normalized_source_text,
            )
            if not outcome.accepted:
                rejected.append(
                    RejectedClaimRecord(
                        category="SKILL",
                        identifier=claim.canonical_skill_id,
                        raw_quote=claim.source_quote,
                        similarity_score=outcome.similarity_score,
                        reason=(
                            f"Similarity {outcome.similarity_score:.3f} < "
                            f"threshold {self._threshold:.2f}."
                        ),
                    )
                )
                continue
            resolved_quote = (
                claim.source_quote
                if outcome.similarity_score == 1.0 and len(claim.source_quote) <= 480
                else outcome.canonical_quote
            )
            calibrated_confidence = min(
                float(claim.confidence),
                max(0.0, min(1.0, outcome.similarity_score)),
            )
            accepted.append(
                ExtractedSkillClaim(
                    canonical_skill_id=claim.canonical_skill_id,
                    raw_label=claim.raw_label,
                    source_quote=resolved_quote,
                    claimed_depth=claim.claimed_depth,
                    applied_months=claim.applied_months,
                    recency_months=claim.recency_months,
                    confidence=ConfidenceScore(calibrated_confidence),
                )
            )
        return tuple(accepted), tuple(rejected)

    def verify_impact_metrics(
        self,
        *,
        metrics: Sequence[ImpactMetric],
        normalized_source_text: str,
    ) -> tuple[tuple[ImpactMetric, ...], tuple[RejectedClaimRecord, ...]]:
        """Verify impact metrics contain verbatim/grounded quotes in normalized CV text."""
        accepted: list[ImpactMetric] = []
        rejected: list[RejectedClaimRecord] = []
        for metric in metrics:
            outcome = self.verify_quote(
                source_quote=metric.source_quote,
                normalized_source_text=normalized_source_text,
            )
            if not outcome.accepted:
                rejected.append(
                    RejectedClaimRecord(
                        category="IMPACT_METRIC",
                        identifier=metric.metric_type,
                        raw_quote=metric.source_quote,
                        similarity_score=outcome.similarity_score,
                        reason="Impact metric quote failed grounding verification.",
                    )
                )
                continue
            resolved_quote = (
                metric.source_quote if outcome.similarity_score == 1.0 else outcome.canonical_quote
            )
            accepted.append(
                ImpactMetric(
                    metric_type=metric.metric_type,
                    numeric_value=metric.numeric_value,
                    unit=metric.unit,
                    source_quote=resolved_quote,
                )
            )
        return tuple(accepted), tuple(rejected)

    @staticmethod
    def validate_chronological_range(
        *,
        start_year: int,
        start_month: int,
        end_year: int | None,
        end_month: int | None,
        is_current: bool,
        as_of: date | None = None,
    ) -> VerifiedWorkDateRange | None:
        """Validate end_date >= start_date chronologically and compute months/recency."""
        if not (1960 <= start_year <= 2100 and 1 <= start_month <= 12):
            return None
        ref = as_of if as_of is not None else utc_now().date()
        ref_idx = ref.year * 12 + (ref.month - 1)
        start_idx = start_year * 12 + (start_month - 1)
        if start_idx > ref_idx:
            return None

        if is_current:
            if end_year is not None or end_month is not None:
                return None
            duration = max(1, ref_idx - start_idx + 1)
            return VerifiedWorkDateRange(
                start_year=start_year,
                start_month=start_month,
                end_year=None,
                end_month=None,
                is_current=True,
                duration_months=min(720, duration),
                recency_months=0,
            )

        if end_year is None or end_month is None:
            return None
        if not (1960 <= end_year <= 2100 and 1 <= end_month <= 12):
            return None
        if (end_year, end_month) < (start_year, start_month):
            return None
        end_idx = end_year * 12 + (end_month - 1)
        duration = max(1, end_idx - start_idx + 1)
        recency = max(0, ref_idx - end_idx)
        return VerifiedWorkDateRange(
            start_year=start_year,
            start_month=start_month,
            end_year=end_year,
            end_month=end_month,
            is_current=False,
            duration_months=min(720, duration),
            recency_months=min(720, recency),
        )
