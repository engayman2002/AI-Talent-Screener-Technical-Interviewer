"""Cognitive LangGraph Nodes: QuestionGenerator, Evaluation, SocraticFollowUp, Verification."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.live_interview.dtos import CompetencyBriefingTargetDTO
from talent_screener.domain.contexts.evaluation_analytics.value_objects import (
    AuthenticityIndex,
    HiringRecommendationBand,
)
from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    PromptHash,
    TurnEvaluationScore,
)
from talent_screener.domain.shared.guards import clamp
from talent_screener.infrastructure.ai_adapters.langgraph_interview.state import (
    EvaluatedTurnRecord,
    InterviewGraphState,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_METRIC_NUMBER_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ms|s|%|qps|rps|mb|gb|tb|x|k|p95|p99)?\b",
    re.I,
)

_DEPTH_MECHANISM_TERMS: frozenset[str] = frozenset(
    {
        "quorum",
        "raft",
        "paxos",
        "wal",
        "log",
        "idempotent",
        "idempotency",
        "backpressure",
        "partition",
        "sharding",
        "shard",
        "replication",
        "replica",
        "consistency",
        "linearizable",
        "eventual",
        "lease",
        "fencing",
        "deadlock",
        "mutex",
        "lock",
        "timeout",
        "backoff",
        "jitter",
        "retry",
        "circuit",
        "breaker",
        "eviction",
        "lru",
        "lfu",
        "ttl",
        "btree",
        "lsm",
        "compaction",
        "index",
        "outbox",
        "saga",
        "isolation",
        "snapshot",
        "serializable",
        "hashing",
        "consistent",
        "checkpoint",
        "offset",
        "consumer",
        "queue",
        "buffer",
        "batch",
        "async",
        "thread",
        "failover",
        "canary",
        "rollback",
    }
)

_TRADEOFF_TERMS: frozenset[str] = frozenset(
    {
        "tradeoff",
        "trade",
        "versus",
        "vs",
        "however",
        "whereas",
        "downside",
        "overhead",
        "cost",
        "bottleneck",
        "latency",
        "throughput",
        "memory",
        "amortized",
        "complexity",
        "risk",
        "failure",
        "split",
        "brain",
        "stale",
        "skew",
    }
)

_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "with",
        "this",
        "from",
        "into",
        "your",
        "have",
        "been",
        "were",
        "what",
        "when",
        "how",
        "would",
        "could",
        "they",
        "their",
        "about",
        "used",
        "using",
        "because",
        "just",
        "very",
        "some",
        "more",
    }
)

_AI_BOILERPLATE_PHRASES: tuple[str, ...] = (
    "in conclusion",
    "furthermore",
    "moreover",
    "additionally",
    "it is important to note",
    "it is crucial to note",
    "in today's landscape",
    "comprehensive overview",
    "holistic approach",
    "delve into",
    "cutting-edge",
    "multifaceted",
    "leveraging robust",
)


def wrap_untrusted_candidate_utterance(
    raw_text: str,
    *,
    turn_number: int,
    competency_skill_id: str,
) -> str:
    """Wrap candidate text in XML delimiter boundaries to prevent instruction breakout."""
    neutralized = (
        raw_text.replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\x00", "")
        .strip()
    )
    safe_skill = re.sub(r"[^a-zA-Z0-9._-]", "_", competency_skill_id)[:64] or "competency"
    return (
        f'<untrusted_candidate_response turn="{turn_number}" competency="{safe_skill}">\n'
        f"{neutralized}\n"
        "</untrusted_candidate_response>"
    )


def extract_xml_delimited_utterance(xml_wrapped: str) -> str:
    """Recover plain candidate text from the strict XML isolation delimiter."""
    match = re.search(
        r"<untrusted_candidate_response\b[^>]*>\s*(.*?)\s*</untrusted_candidate_response>",
        xml_wrapped,
        flags=re.DOTALL,
    )
    body = match.group(1) if match is not None else xml_wrapped
    return body.replace("&lt;", "<").replace("&gt;", ">").strip()


def _trim_word_budget(text: str, *, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words)
    trimmed = " ".join(words[:max_words]).rstrip(",;:")
    if not trimmed.endswith("?"):
        trimmed = f"{trimmed}?"
    return trimmed


@dataclass(frozen=True, slots=True)
class QuestionGeneratorInput:
    """Read-only context supplied to QuestionGenerationModelProtocol."""

    competency: CompetencyBriefingTargetDTO
    difficulty: DifficultyLevel
    project_highlights: tuple[str, ...]
    turn_number: int
    prior_questions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GeneratedQuestionPayload:
    """Structured prompt generation result before PromptHash sealing."""

    question_text: str
    tokens_used: int


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    """XML-delimited input supplied to TurnEvaluationModelProtocol."""

    competency: CompetencyBriefingTargetDTO
    difficulty: DifficultyLevel
    turn_number: int
    active_question: str
    xml_delimited_response: str
    project_highlights: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CognitiveEvaluationPayload:
    """Structured normalized & 5-point evaluation outcome returned by EvaluationNode."""

    accuracy_norm: float
    depth_norm: float
    tradeoff_norm: float
    specificity_norm: float
    relevance_norm: float
    weighted_turn_score: float
    turn_evaluation: TurnEvaluationScore
    stylometric_suspicion: float
    tokens_used: int
    rationale: str


@dataclass(frozen=True, slots=True)
class SocraticProbeInput:
    """Target context supplied to SocraticProbeModelProtocol."""

    competency: CompetencyBriefingTargetDTO
    difficulty: DifficultyLevel
    probe_index: int
    strategy: str
    target_concept: str
    sanitized_candidate_utterance: str
    rationale: str


@dataclass(frozen=True, slots=True)
class SocraticProbePayload:
    """Generated concise Socratic follow-up probe."""

    strategy: str
    target_concept: str
    probe_prompt: str
    tokens_used: int


@dataclass(frozen=True, slots=True)
class SynthesisVerificationPayload:
    """Final graph-level mathematical synthesis and audit summary."""

    technical_interview_score: float
    authenticity_index: float
    authenticity_moderator: float
    recommendation_band_preview: HiringRecommendationBand
    competency_mastery: dict[UUID, float]
    summary_text: str
    tokens_used: int


@runtime_checkable
class QuestionGenerationModelProtocol(Protocol):
    """Injectable LLM driver contract for QuestionGeneratorNode."""

    async def generate_question(self, payload: QuestionGeneratorInput) -> GeneratedQuestionPayload:
        ...


@runtime_checkable
class TurnEvaluationModelProtocol(Protocol):
    """Injectable LLM driver contract for EvaluationNode."""

    async def evaluate_response(self, payload: EvaluationInput) -> CognitiveEvaluationPayload:
        ...


@runtime_checkable
class SocraticProbeModelProtocol(Protocol):
    """Injectable LLM driver contract for SocraticFollowUpNode."""

    async def generate_probe(self, payload: SocraticProbeInput) -> SocraticProbePayload:
        ...


@runtime_checkable
class InterviewSynthesisModelProtocol(Protocol):
    """Injectable verification driver contract for ScoringVerificationNode."""

    async def synthesize(
        self,
        *,
        state: InterviewGraphState,
        evaluations: Sequence[EvaluatedTurnRecord],
    ) -> SynthesisVerificationPayload:
        ...


class DeterministicQuestionGenerationModel:
    """Zero-network deterministic question generator calibrated to L1..L5 & projects."""

    _DIFFICULTY_FOCUS: ClassVar[dict[DifficultyLevel, str]] = {
        DifficultyLevel.L1: "core mechanics and data flow",
        DifficultyLevel.L2: "implementation choices and failure handling",
        DifficultyLevel.L3: (
            "operational bottlenecks, capacity limits, and consistency guarantees"
        ),
        DifficultyLevel.L4: (
            "distributed failure modes, p99 tail-latency mitigation, and tradeoffs"
        ),
        DifficultyLevel.L5: (
            "multi-region isolation, consensus invariants, and adversarial edge cases"
        ),
    }

    async def generate_question(self, payload: QuestionGeneratorInput) -> GeneratedQuestionPayload:
        focus = self._DIFFICULTY_FOCUS[payload.difficulty]
        skill = payload.competency.canonical_skill_id
        anchor = payload.competency.ideal_anchor_descriptor
        project_clause = (
            f"Drawing from your experience ({payload.project_highlights[0][:90]}), "
            if payload.project_highlights
            else ""
        )
        question = (
            f"{project_clause}for {skill} ({anchor}) at L{int(payload.difficulty)}, "
            f"how do you engineer {focus} and quantify the key architectural tradeoffs?"
        )
        # Ensure distinct prompt text when advancing primary turns on the same skill
        if question in payload.prior_questions:
            question = (
                f"Stepping deeper at L{int(payload.difficulty)} on {skill}: what subtle failure "
                f"scenario breaks {anchor}, and how do you verify recovery bounds?"
            )
        concise = _trim_word_budget(question, max_words=75)
        estimated_tokens = max(90, len(concise.split()) * 8)
        return GeneratedQuestionPayload(question_text=concise, tokens_used=estimated_tokens)


class DeterministicTurnEvaluationModel:
    """Rule-anchored deterministic evaluator computing rubric axes and stylometric suspicion."""

    async def evaluate_response(self, payload: EvaluationInput) -> CognitiveEvaluationPayload:
        plain = extract_xml_delimited_utterance(payload.xml_delimited_response)
        lowered = plain.lower()
        words = _TOKEN_RE.findall(lowered)
        word_set = set(words)
        word_count = len(words)

        anchor_text = (
            f"{payload.competency.canonical_skill_id} "
            f"{payload.competency.ideal_anchor_descriptor}"
        ).lower()
        anchor_tokens = {
            tok
            for tok in _TOKEN_RE.findall(anchor_text)
            if len(tok) >= 3 and tok not in _STOP_WORDS
        }
        project_tokens = {
            tok
            for proj in payload.project_highlights
            for tok in _TOKEN_RE.findall(proj.lower())
            if len(tok) >= 4 and tok not in _STOP_WORDS
        }

        anchor_hits = len(word_set & anchor_tokens)
        mechanism_hits = len(word_set & _DEPTH_MECHANISM_TERMS)
        tradeoff_hits = len(word_set & _TRADEOFF_TERMS)
        metric_hits = len(_METRIC_NUMBER_RE.findall(plain))
        project_hits = len(word_set & project_tokens)
        boilerplate_hits = sum(1 for phrase in _AI_BOILERPLATE_PHRASES if phrase in lowered)

        if word_count <= 8:
            acc_norm = clamp(0.18 + 0.04 * anchor_hits, 0.10, 0.32)
            depth_norm = clamp(0.15 + 0.05 * mechanism_hits, 0.10, 0.30)
            tradeoff_norm = 0.15
            spec_norm = clamp(0.15 + 0.06 * metric_hits, 0.10, 0.30)
            rel_norm = clamp(0.25 + 0.10 * anchor_hits, 0.20, 0.45)
        elif word_count < 24 and tradeoff_hits == 0 and metric_hits == 0:
            # Shallow / buzzword answer: Q_eff lands in [0.40, 0.64) and specificity < 2.5
            acc_norm = clamp(
                0.55 + 0.05 * min(anchor_hits, 3) + 0.03 * min(mechanism_hits, 3),
                0.45,
                0.68,
            )
            depth_norm = clamp(0.42 + 0.05 * min(mechanism_hits, 3), 0.35, 0.60)
            tradeoff_norm = 0.35
            spec_norm = clamp(0.32 + 0.04 * min(project_hits, 2), 0.28, 0.44)
            rel_norm = clamp(0.60 + 0.08 * min(anchor_hits, 3), 0.50, 0.85)
        else:
            length_bonus = min(0.18, word_count / 250.0)
            acc_norm = clamp(
                0.56 + 0.06 * min(anchor_hits, 4) + 0.05 * min(mechanism_hits, 4) + length_bonus,
                0.35,
                0.98,
            )
            depth_norm = clamp(
                0.46 + 0.09 * min(mechanism_hits, 4) + 0.05 * min(metric_hits, 3) + length_bonus,
                0.25,
                0.98,
            )
            tradeoff_norm = clamp(
                0.42 + 0.13 * min(tradeoff_hits, 4) + 0.04 * min(mechanism_hits, 2),
                0.20,
                0.98,
            )
            spec_norm = clamp(
                0.38
                + 0.14 * min(metric_hits, 3)
                + 0.09 * min(mechanism_hits, 3)
                + 0.08 * min(project_hits, 2),
                0.20,
                0.98,
            )
            rel_norm = clamp(
                0.58 + 0.09 * min(anchor_hits, 4) + 0.04 * min(mechanism_hits, 2),
                0.30,
                0.98,
            )

        # Stylometric suspicion rises when generic LLM template transitions cluster ungrounded
        stylo = clamp(
            0.06
            + 0.24 * boilerplate_hits
            + (0.14 if boilerplate_hits >= 2 and metric_hits == 0 and project_hits == 0 else 0.0),
            0.0,
            0.95,
        )

        eval_score = TurnEvaluationScore(
            technical_accuracy=round(acc_norm * 5.0, 4),
            depth_of_understanding=round(depth_norm * 5.0, 4),
            tradeoff_awareness=round(tradeoff_norm * 5.0, 4),
            contextual_specificity=round(spec_norm * 5.0, 4),
            suspicion_score=round(stylo, 4),
        )
        weighted_turn = eval_score.effective_quality_score() * 0.85 + rel_norm * 0.15
        tokens_used = max(140, (word_count + len(payload.active_question.split())) * 4)

        return CognitiveEvaluationPayload(
            accuracy_norm=acc_norm,
            depth_norm=depth_norm,
            tradeoff_norm=tradeoff_norm,
            specificity_norm=spec_norm,
            relevance_norm=rel_norm,
            weighted_turn_score=clamp(weighted_turn, 0.0, 1.0),
            turn_evaluation=eval_score,
            stylometric_suspicion=stylo,
            tokens_used=tokens_used,
            rationale=(
                f"L{int(payload.difficulty)} {payload.competency.canonical_skill_id}: "
                f"Q_eff={eval_score.effective_quality_score():.2f}, "
                f"mechanisms={mechanism_hits}, tradeoffs={tradeoff_hits}, metrics={metric_hits}."
            ),
        )


class DeterministicSocraticProbeModel:
    """Deterministic probe generator enforcing domain strategy alignment and <=65 word cap."""

    _STRATEGY_ALIASES: ClassVar[dict[str, str]] = {
        "CONCRETE_EXAMPLE_PROBE": "CLARIFY_VAGUE_CLAIM",
        "EDGE_CASE_CHALLENGE": "UNSTATED_TRADEOFF_PROBE",
        "ARCHITECTURAL_TRADEOFF_PROBE": "UNSTATED_TRADEOFF_PROBE",
        "SHALLOW_BUZZWORD_PROBE": "SHALLOW_BUZZWORD_PROBE",
        "CLARIFY_VAGUE_CLAIM": "CLARIFY_VAGUE_CLAIM",
        "UNSTATED_TRADEOFF_PROBE": "UNSTATED_TRADEOFF_PROBE",
        "AUTHENTICITY_VERIFICATION_PROBE": "AUTHENTICITY_VERIFICATION_PROBE",
    }

    async def generate_probe(self, payload: SocraticProbeInput) -> SocraticProbePayload:
        canonical_strategy = self._STRATEGY_ALIASES.get(
            payload.strategy.upper(), "UNSTATED_TRADEOFF_PROBE"
        )
        claimed_terms = [
            tok
            for tok in _TOKEN_RE.findall(payload.sanitized_candidate_utterance.lower())
            if tok in _DEPTH_MECHANISM_TERMS or tok in _TRADEOFF_TERMS
        ]
        claimed_focus = claimed_terms[0] if claimed_terms else payload.target_concept
        skill = payload.competency.canonical_skill_id

        if canonical_strategy == "AUTHENTICITY_VERIFICATION_PROBE":
            prompt = (
                f"Walk me step-by-step through a specific outage or debugging edge case in your "
                f"own {claimed_focus} implementation—what telemetry metric broke first and what "
                f"tradeoff forced your fix?"
            )
        elif canonical_strategy == "SHALLOW_BUZZWORD_PROBE":
            prompt = (
                f"You referenced {claimed_focus}, yet omitted failure bounds. Which concrete "
                f"bottleneck limits throughput at L{int(payload.difficulty)}, and what metric "
                "proves it?"
            )
        elif canonical_strategy == "CLARIFY_VAGUE_CLAIM":
            prompt = (
                f"Give one concrete production incident involving {claimed_focus} in "
                f"{skill}, detailing tail latency numbers and fallback behavior?"
            )
        else:
            prompt = (
                f"Focusing strictly on {claimed_focus} under {skill}: "
                f"what operational downside occurs during peak load or partial partitions, "
                f"and how would you mitigate it?"
            )

        concise = _trim_word_budget(prompt, max_words=65)
        return SocraticProbePayload(
            strategy=canonical_strategy,
            target_concept=payload.target_concept,
            probe_prompt=concise,
            tokens_used=max(95, len(concise.split()) * 7),
        )


class DeterministicInterviewSynthesisModel:
    """Mathematical verifier aggregating mastery M_k, S_interview, and I_auth across Plane 4."""

    async def synthesize(
        self,
        *,
        state: InterviewGraphState,
        evaluations: Sequence[EvaluatedTurnRecord],
    ) -> SynthesisVerificationPayload:
        weighted_num = 0.0
        weighted_den = 0.0
        mastery_map: dict[UUID, float] = {}

        for target in state.rubric.competency_queue:
            comp_evals = [
                item for item in evaluations if item.competency_id == target.competency_id
            ]
            if not comp_evals:
                m_k = state.routing.mastery_by_competency.get(target.competency_id, 0.0)
            else:
                demonstrated = [
                    float(int(item.difficulty))
                    * item.evaluation.effective_quality_score()
                    * (1.0 - 0.08 * item.probe_index)
                    for item in comp_evals
                ]
                peak = max(demonstrated)
                lvl_sum = sum(float(int(item.difficulty)) for item in comp_evals)
                weighted = sum(
                    float(int(item.difficulty)) * d_dem
                    for item, d_dem in zip(comp_evals, demonstrated, strict=True)
                )
                ratio = weighted / lvl_sum if lvl_sum > 0.0 else 0.0
                m_k = clamp(0.70 * peak + 0.30 * ratio, 0.0, 5.0)
            mastery_map[target.competency_id] = m_k
            attainment = min(1.0, m_k / float(max(1, target.target_depth))) * 100.0
            weighted_num += target.weight * attainment
            weighted_den += target.weight

        s_interview = clamp(weighted_num / weighted_den if weighted_den > 0.0 else 0.0, 0.0, 100.0)
        clamped_auth = clamp(state.routing.rolling_authenticity_index, 0.0, 1.0)
        auth_idx = AuthenticityIndex(score=clamped_auth)
        phi_auth = auth_idx.authenticity_moderator()
        effective_band_score = s_interview * phi_auth

        if effective_band_score >= 82.0 and not auth_idx.is_high_risk:
            band = HiringRecommendationBand.STRONG_HIRE
        elif effective_band_score >= 68.0 and not auth_idx.is_high_risk:
            band = HiringRecommendationBand.HIRE
        elif effective_band_score >= 50.0:
            band = HiringRecommendationBand.BORDERLINE_HOLD
        else:
            band = HiringRecommendationBand.NO_HIRE

        summary = (
            f"Verified {len(evaluations)} turn(s) across {len(state.rubric.competency_queue)} "
            f"competency target(s): S_interview={s_interview:.2f}, "
            f"I_auth={auth_idx.score:.2f} (Phi={phi_auth:.2f}), preview={band.value}."
        )
        return SynthesisVerificationPayload(
            technical_interview_score=round(s_interview, 4),
            authenticity_index=round(auth_idx.score, 4),
            authenticity_moderator=round(phi_auth, 4),
            recommendation_band_preview=band,
            competency_mastery=mastery_map,
            summary_text=summary,
            tokens_used=60,
        )


class QuestionGeneratorNode:
    """Cognitive node producing unique difficulty-calibrated interview questions + PromptHash."""

    def __init__(self, model: QuestionGenerationModelProtocol | None = None) -> None:
        self._model: QuestionGenerationModelProtocol = (
            model if model is not None else DeterministicQuestionGenerationModel()
        )

    async def run(
        self,
        *,
        competency: CompetencyBriefingTargetDTO,
        difficulty: DifficultyLevel,
        project_highlights: tuple[str, ...],
        turn_number: int,
        prior_questions: tuple[str, ...] = (),
        prior_prompt_hashes: Sequence[PromptHash] = (),
    ) -> tuple[str, PromptHash, int]:
        payload = QuestionGeneratorInput(
            competency=competency,
            difficulty=difficulty,
            project_highlights=project_highlights,
            turn_number=turn_number,
            prior_questions=prior_questions,
        )
        generated = await self._model.generate_question(payload)
        prompt_text = _trim_word_budget(generated.question_text, max_words=75)
        prompt_hash = PromptHash.from_prompt_text(prompt_text)

        existing_hexes = {h.sha256.value for h in prior_prompt_hashes}
        suffix_idx = 1
        while prompt_hash.sha256.value in existing_hexes:
            candidate = _trim_word_budget(
                f"{prompt_text.rstrip('?')} under fault condition #{suffix_idx}?",
                max_words=75,
            )
            prompt_text = candidate
            prompt_hash = PromptHash.from_prompt_text(prompt_text)
            suffix_idx += 1

        return prompt_text, prompt_hash, generated.tokens_used


class EvaluationNode:
    """Cognitive node evaluating XML-isolated candidate responses against the locked rubric."""

    def __init__(self, model: TurnEvaluationModelProtocol | None = None) -> None:
        self._model: TurnEvaluationModelProtocol = (
            model if model is not None else DeterministicTurnEvaluationModel()
        )

    async def run(
        self,
        *,
        competency: CompetencyBriefingTargetDTO,
        difficulty: DifficultyLevel,
        turn_number: int,
        active_question: str,
        sanitized_candidate_utterance: str,
        project_highlights: tuple[str, ...],
    ) -> CognitiveEvaluationPayload:
        wrapped = wrap_untrusted_candidate_utterance(
            sanitized_candidate_utterance,
            turn_number=turn_number,
            competency_skill_id=competency.canonical_skill_id,
        )
        return await self._model.evaluate_response(
            EvaluationInput(
                competency=competency,
                difficulty=difficulty,
                turn_number=turn_number,
                active_question=active_question,
                xml_delimited_response=wrapped,
                project_highlights=project_highlights,
            )
        )


class SocraticFollowUpNode:
    """Cognitive node generating targeted Socratic probes without duplicating prompt hashes."""

    def __init__(self, model: SocraticProbeModelProtocol | None = None) -> None:
        self._model: SocraticProbeModelProtocol = (
            model if model is not None else DeterministicSocraticProbeModel()
        )

    async def run(
        self,
        *,
        competency: CompetencyBriefingTargetDTO,
        difficulty: DifficultyLevel,
        probe_index: int,
        strategy: str,
        sanitized_candidate_utterance: str,
        rationale: str,
        prior_prompt_hashes: Sequence[PromptHash] = (),
    ) -> tuple[str, str, PromptHash, int]:
        payload = SocraticProbeInput(
            competency=competency,
            difficulty=difficulty,
            probe_index=probe_index,
            strategy=strategy,
            target_concept=competency.canonical_skill_id,
            sanitized_candidate_utterance=sanitized_candidate_utterance,
            rationale=rationale,
        )
        generated = await self._model.generate_probe(payload)
        probe_prompt = _trim_word_budget(generated.probe_prompt, max_words=65)
        prompt_hash = PromptHash.from_prompt_text(probe_prompt)

        existing_hexes = {h.sha256.value for h in prior_prompt_hashes}
        suffix_idx = 1
        while prompt_hash.sha256.value in existing_hexes:
            probe_prompt = _trim_word_budget(
                f"{probe_prompt.rstrip('?')} with quantitative scenario #{suffix_idx}?",
                max_words=65,
            )
            prompt_hash = PromptHash.from_prompt_text(probe_prompt)
            suffix_idx += 1

        return generated.strategy, probe_prompt, prompt_hash, generated.tokens_used


class ScoringVerificationNode:
    """Final cognitive verification node synthesizing Plane 4 mastery & authenticity."""

    def __init__(self, model: InterviewSynthesisModelProtocol | None = None) -> None:
        self._model: InterviewSynthesisModelProtocol = (
            model if model is not None else DeterministicInterviewSynthesisModel()
        )

    async def run(self, state: InterviewGraphState) -> SynthesisVerificationPayload:
        return await self._model.synthesize(
            state=state,
            evaluations=state.ledger.evaluations,
        )
