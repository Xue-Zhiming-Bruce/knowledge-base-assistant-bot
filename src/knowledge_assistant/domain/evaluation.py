"""Versioned synthetic evaluation records, deterministic controls, and metrics."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class EvaluationChunk:
    generation_id: str
    chunk_id: str
    document_id: str
    revision_id: str
    content: str
    content_fingerprint: str
    token_count: int
    source_provider: str


@dataclass(frozen=True, slots=True)
class GeneratedQuestion:
    question: str
    reference_answer: str
    required_facts: tuple[str, ...]
    supporting_excerpt: str
    question_type: str
    difficulty: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    question_style: str | None = None
    lexical_overlap_ratio: float | None = None
    supporting_chunk_count: int | None = None
    requires_decomposition: bool | None = None


@dataclass(frozen=True, slots=True)
class SyntheticEvaluationCase:
    case_id: str
    dataset_version: str
    target_chunk_id: str | None
    target_document_id: str | None
    target_revision_id: str | None
    content_fingerprint: str | None
    question: str
    reference_answer: str
    required_facts: tuple[str, ...]
    supporting_excerpt: str
    acceptable_chunk_ids: tuple[str, ...]
    source_provider: str
    question_type: str
    difficulty: str
    generator_model: str
    generator_prompt_version: str
    question_style: str | None = None
    naturalizer_model: str | None = None
    naturalizer_prompt_version: str | None = None
    lexical_overlap_ratio: float | None = None
    supporting_chunk_count: int | None = None
    requires_decomposition: bool | None = None
    no_answer: bool = False
    distractor_chunk_ids: tuple[str, ...] = ()
    document_level: bool = False
    target_url: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SyntheticEvaluationCase:
        def required_string(key: str) -> str:
            item = value.get(key)
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"evaluation case requires non-empty {key}")
            return item

        def optional_string(key: str, *, default: str | None = None) -> str | None:
            item = value.get(key)
            if not isinstance(item, str) or not item.strip():
                return default
            return item

        def string_list(key: str) -> tuple[str, ...]:
            item = value.get(key)
            if item is None:
                return ()
            if (
                not isinstance(item, (list, tuple))
                or any(not isinstance(entry, str) or not entry.strip() for entry in item)
            ):
                raise ValueError(
                    f"evaluation case requires {key} to be a list of non-empty strings"
                )
            return tuple(item)

        def optional_float(key: str) -> float | None:
            item = value.get(key)
            if item is None:
                return None
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"evaluation case requires {key} to be a number")
            return float(item)

        def optional_int(key: str) -> int | None:
            item = value.get(key)
            if item is None:
                return None
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError(f"evaluation case requires {key} to be an integer")
            return item

        def optional_bool(key: str, *, default: bool | None = None) -> bool | None:
            item = value.get(key)
            if item is None:
                return default
            if not isinstance(item, bool):
                raise ValueError(f"evaluation case requires {key} to be a boolean")
            return item

        return cls(
            case_id=required_string("case_id"),
            dataset_version=required_string("dataset_version"),
            target_chunk_id=optional_string("target_chunk_id"),
            target_document_id=optional_string("target_document_id"),
            target_revision_id=optional_string("target_revision_id"),
            content_fingerprint=optional_string("content_fingerprint"),
            question=required_string("question"),
            reference_answer=required_string("reference_answer"),
            required_facts=string_list("required_facts"),
            supporting_excerpt=optional_string("supporting_excerpt", default="") or "",
            acceptable_chunk_ids=string_list("acceptable_chunk_ids"),
            source_provider=required_string("source_provider"),
            question_type=required_string("question_type"),
            difficulty=required_string("difficulty"),
            generator_model=required_string("generator_model"),
            generator_prompt_version=required_string("generator_prompt_version"),
            question_style=optional_string("question_style"),
            naturalizer_model=optional_string("naturalizer_model"),
            naturalizer_prompt_version=optional_string("naturalizer_prompt_version"),
            lexical_overlap_ratio=optional_float("lexical_overlap_ratio"),
            supporting_chunk_count=optional_int("supporting_chunk_count"),
            requires_decomposition=optional_bool("requires_decomposition"),
            no_answer=optional_bool("no_answer", default=False) or False,
            distractor_chunk_ids=string_list("distractor_chunk_ids"),
            document_level=optional_bool("document_level", default=False) or False,
            target_url=optional_string("target_url"),
        )


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationResult:
    case_id: str
    strategy: str
    target_rank: int | None
    hit_at_5: bool
    hit_at_20: bool
    reciprocal_rank: float
    retrieved_chunk_ids: tuple[str, ...]
    route: str
    subqueries: tuple[str, ...]
    retrieval_rounds: int
    stop_reason: str
    latency_seconds: float
    no_answer: bool = False
    false_positive: bool | None = None
    planner_calls: int = 0
    planner_input_tokens: int | None = None
    planner_output_tokens: int | None = None
    case_type: str = ""
    case_difficulty: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RetrievalSliceBreakdown:
    """Public-safe aggregates for one question-type or difficulty slice."""

    label: str
    cases: int
    answerable_cases: int
    hit_at_5: float
    hit_at_20: float
    mean_reciprocal_rank: float
    mean_latency_seconds: float
    no_answer_cases: int
    no_answer_false_positive_rate: float | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationSummary:
    dataset_version: str
    strategy: str
    cases: int
    hit_at_5: float
    hit_at_20: float
    mean_reciprocal_rank: float
    mean_latency_seconds: float
    no_answer_cases: int = 0
    no_answer_false_positive_rate: float | None = None
    mean_planner_calls: float = 0.0
    mean_planner_input_tokens: float = 0.0
    mean_planner_output_tokens: float = 0.0
    by_question_type: tuple[RetrievalSliceBreakdown, ...] = ()
    by_difficulty: tuple[RetrievalSliceBreakdown, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AnswerJudgeResult:
    """Structured judge verdict for one answer against a fixed rubric."""

    model: str
    prompt_version: str
    rubric_version: str
    factual_correctness: int
    groundedness: int
    completeness: int
    relevance_concision: int
    uncertainty: int
    overall: int
    required_fact_support: tuple[tuple[str, bool], ...]
    justification: str


@dataclass(frozen=True, slots=True)
class AnswerEvaluationResult:
    """Private per-(case, answer approach) record of the full RAG path."""

    case_id: str
    approach: str
    retrieval_strategy: str
    question: str
    question_type: str
    difficulty: str
    no_answer: bool
    answer: str
    sufficient_evidence: bool
    abstained_correctly: bool | None
    unexpected_abstention: bool
    declared_citations: tuple[str, ...]
    marker_citations: tuple[str, ...]
    resolved_citations: tuple[str, ...]
    citation_error: str | None
    citation_coverage: float
    required_fact_lexical_coverage: float
    generation_model: str
    generation_input_tokens: int | None
    generation_output_tokens: int | None
    generation_latency_seconds: float
    total_latency_seconds: float
    judge: AnswerJudgeResult | None = None
    judge_error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AnswerSliceBreakdown:
    """Aggregates for one question-type or difficulty slice."""

    label: str
    cases: int
    citation_validity_rate: float
    citation_coverage_mean: float
    no_answer_abstention_rate: float | None
    mean_factual_correctness: float | None
    mean_groundedness: float | None
    mean_completeness: float | None
    mean_relevance_concision: float | None
    mean_uncertainty: float | None
    mean_overall: float | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AnswerEvaluationSummary:
    """Public-safe aggregate for one answer approach."""

    dataset_version: str
    approach: str
    retrieval_strategy: str
    cases: int
    answerable_cases: int
    no_answer_cases: int
    citation_validity_rate: float
    citation_coverage_mean: float
    required_fact_lexical_coverage_mean: float
    no_answer_abstention_rate: float | None
    unexpected_abstention_rate: float
    mean_generation_latency_seconds: float
    mean_total_latency_seconds: float
    mean_generation_input_tokens: float
    mean_generation_output_tokens: float
    judge_applied: bool
    judge_rubric_version: str | None
    judge_model: str | None
    mean_factual_correctness: float | None
    mean_groundedness: float | None
    mean_completeness: float | None
    mean_relevance_concision: float | None
    mean_uncertainty: float | None
    mean_overall: float | None
    by_question_type: tuple[AnswerSliceBreakdown, ...]
    by_difficulty: tuple[AnswerSliceBreakdown, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class QuestionValidation:
    valid: bool
    reasons: tuple[str, ...]
    lexical_overlap_ratio: float
    longest_shared_phrase: str | None


@dataclass(frozen=True, slots=True)
class DifficultyProperties:
    lexical_overlap_ratio: float
    required_fact_count: int
    supporting_chunk_count: int
    requires_decomposition: bool
    question_token_count: int


_WORD_PATTERN = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(_WORD_PATTERN.findall(text.lower()))


def lexical_overlap_ratio(question: str, source: str) -> float:
    """Fraction of question tokens that also occur in the source chunk."""

    question_tokens = tokenize(question)
    if not question_tokens:
        return 0.0
    source_tokens = frozenset(tokenize(source))
    return sum(1 for token in question_tokens if token in source_tokens) / len(question_tokens)


def longest_shared_phrase(question: str, source: str, *, min_tokens: int = 8) -> str | None:
    """Return the longest contiguous token run shared verbatim, when it is long enough."""

    if min_tokens < 1:
        raise ValueError("min_tokens must be positive")
    question_tokens = tokenize(question)
    source_tokens = tokenize(source)
    if not question_tokens or not source_tokens:
        return None
    best_length = 0
    best_end = 0
    previous = [0] * (len(source_tokens) + 1)
    for index, question_token in enumerate(question_tokens, start=1):
        current = [0] * (len(source_tokens) + 1)
        for source_index, source_token in enumerate(source_tokens, start=1):
            if question_token == source_token:
                current[source_index] = previous[source_index - 1] + 1
                if current[source_index] > best_length:
                    best_length = current[source_index]
                    best_end = index
        previous = current
    if best_length >= min_tokens:
        return " ".join(question_tokens[best_end - best_length : best_end])
    return None


class DeterministicQuestionValidator:
    """Reject questions that leak the source chunk through wording or phrase reuse."""

    VERSION = "deterministic-question-validator-v1"

    FORBIDDEN_SOURCE_WORDING: tuple[str, ...] = (
        "the passage",
        "the chunk",
        "the article",
        "the source",
        "this passage",
        "this chunk",
        "this article",
        "this excerpt",
        "the excerpt",
        "this text",
        "the text above",
        "the provided",
        "the supplied",
        "the given",
        "source chunk",
        "according to the",
        "as mentioned",
        "as described",
        "in the passage",
        "in the article",
        "in the chunk",
        "from the article",
        "from the passage",
        "from the chunk",
        "the following text",
    )

    def __init__(
        self,
        *,
        max_lexical_overlap: float = 0.9,
        min_long_phrase_tokens: int = 8,
    ) -> None:
        if not 0.0 < max_lexical_overlap <= 1.0:
            raise ValueError("max_lexical_overlap must be between 0 and 1")
        if min_long_phrase_tokens < 4:
            raise ValueError("min_long_phrase_tokens must be at least 4")
        self._max_lexical_overlap = max_lexical_overlap
        self._min_long_phrase_tokens = min_long_phrase_tokens

    def validate(self, question: str, source: str) -> QuestionValidation:
        question = question.strip()
        reasons: list[str] = []
        if not question:
            reasons.append("question is blank")
        forbidden = self.forbidden_source_wording(question)
        if forbidden:
            reasons.append(f"question uses source-oriented wording: {', '.join(forbidden)}")
        overlap = lexical_overlap_ratio(question, source)
        phrase = longest_shared_phrase(question, source, min_tokens=self._min_long_phrase_tokens)
        if phrase is not None:
            reasons.append(f"question copies a long distinctive phrase: '{phrase}'")
        if overlap > self._max_lexical_overlap:
            reasons.append(
                f"question lexical overlap {overlap:.2f} exceeds {self._max_lexical_overlap:.2f}"
            )
        return QuestionValidation(
            valid=not reasons,
            reasons=tuple(reasons),
            lexical_overlap_ratio=overlap,
            longest_shared_phrase=phrase,
        )

    def forbidden_source_wording(self, question: str) -> tuple[str, ...]:
        words = tokenize(question)
        matched: list[str] = []
        for phrase in self.FORBIDDEN_SOURCE_WORDING:
            phrase_words = tuple(phrase.split())
            if any(
                words[index : index + len(phrase_words)] == phrase_words
                for index in range(len(words) - len(phrase_words) + 1)
            ):
                matched.append(phrase)
        return tuple(sorted(set(matched)))


def measure_difficulty(
    *,
    question: str,
    source: str,
    required_facts: tuple[str, ...],
    question_type: str,
    supporting_chunk_count: int,
) -> DifficultyProperties:
    """Record measurable difficulty without relying only on the model label."""

    tokens = tokenize(question)
    decomposition_markers = {
        "compare",
        "comparison",
        "differences",
        "difference",
        "versus",
        "vs",
    }
    requires_decomposition = (
        question_type == "comparison"
        or bool(decomposition_markers.intersection(tokens))
        or len(tokens) >= 24
    )
    return DifficultyProperties(
        lexical_overlap_ratio=lexical_overlap_ratio(question, source),
        required_fact_count=len(required_facts),
        supporting_chunk_count=supporting_chunk_count,
        requires_decomposition=requires_decomposition,
        question_token_count=len(tokens),
    )


@dataclass(frozen=True, slots=True)
class SampleSource:
    """Public-safe source record: URL, title, and provenance only."""

    source_id: str
    title: str
    url: str
    provider: str
    provenance: str


@dataclass(frozen=True, slots=True)
class SampleCase:
    """Public-safe curated evaluation question mapped to a sample source."""

    case_id: str
    source_id: str | None
    question: str
    reference_answer: str
    required_facts: tuple[str, ...]
    question_type: str
    difficulty: str
    no_answer: bool = False
    distractor_source_id: str | None = None


@dataclass(frozen=True, slots=True)
class SampleManifest:
    manifest_version: str
    dataset_version: str
    description: str
    sources: tuple[SampleSource, ...]
    cases: tuple[SampleCase, ...]
