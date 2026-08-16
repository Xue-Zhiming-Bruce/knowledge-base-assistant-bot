"""Synthetic-question-v2 generator, deterministic controls, and no-answer cases."""

from __future__ import annotations

import uuid
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from knowledge_assistant.application.evaluation import (
    RetrievalEvaluationRunner,
    SyntheticDatasetBuilder,
)
from knowledge_assistant.domain.evaluation import (
    DeterministicQuestionValidator,
    EvaluationChunk,
    GeneratedQuestion,
    SyntheticEvaluationCase,
    lexical_overlap_ratio,
    longest_shared_phrase,
)
from knowledge_assistant.domain.query import Evidence
from knowledge_assistant.domain.retrieval import (
    QueryRoute,
    RetrievalResult,
    RetrievalStrategyName,
    RetrievalTrace,
)
from knowledge_assistant.infrastructure.openai.evaluation import (
    OpenAISyntheticQuestionGeneratorV2,
    OpenAISyntheticQuestionNaturalizer,
)

GENERATION_ID = uuid.uuid4()

SOURCE = (
    "Tempo wallets hold scoped spending permissions instead of raw private keys. "
    "A Tempo wallet can authorize a single request with a maximum spend cap, so "
    "the user never exposes a wallet private key to a third-party service. The "
    "Tempo request protocol signs each payment separately and expires the "
    "authorization after the requested duration."
)

GOOD_PAYLOAD: dict[str, Any] = {
    "question": "Why do Tempo wallets avoid exposing a private key?",
    "reference_answer": "They use scoped spending permissions instead.",
    "required_facts": ["Tempo wallets use scoped spending permissions."],
    "supporting_excerpt": (
        "Tempo wallets hold scoped spending permissions instead of raw private keys"
    ),
    "supporting_chunk_count": 1,
    "question_type": "explanation",
    "difficulty": "medium",
}


def chunk(*, chunk_id: str = "chunk-a") -> EvaluationChunk:
    return EvaluationChunk(
        generation_id=str(GENERATION_ID),
        chunk_id=chunk_id,
        document_id="doc-a",
        revision_id="rev-a",
        content=SOURCE,
        content_fingerprint="sha256:" + "a" * 64,
        token_count=120,
        source_provider="substack",
    )


class FakeCorpus:
    def active_generation_id(self) -> uuid.UUID:
        return GENERATION_ID

    def sample_chunks(self, **_kwargs: object) -> tuple[EvaluationChunk, ...]:
        return (chunk(),)

    def validate_chunk(self, **kwargs: object) -> bool:
        return kwargs["content_fingerprint"] == chunk().content_fingerprint

    def document_chunks(self, **_kwargs: object) -> tuple[str, ...]:
        return ()

    def document_id_for_url(self, **_kwargs: object) -> str | None:
        return None


class FakeV2Responses:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = payloads
        self.calls = 0

    def parse(self, **_kwargs: Any) -> SimpleNamespace:
        payload = self._payloads[min(self.calls, len(self._payloads) - 1)]
        self.calls += 1
        return SimpleNamespace(
            output_parsed=SimpleNamespace(**payload),
            usage=SimpleNamespace(input_tokens=30, output_tokens=15),
            model="generator-v2",
        )


def v2_generator(
    responses: FakeV2Responses,
    *,
    style_weights: dict[str, float] | None = None,
) -> OpenAISyntheticQuestionGeneratorV2:
    return OpenAISyntheticQuestionGeneratorV2(
        api_key="unused",
        model="generator-v2",
        client=cast(Any, SimpleNamespace(responses=responses)),
        style_weights=style_weights,
    )


class FakeV2Generator:
    PROMPT_VERSION = "synthetic-question-v2"

    def generate(self, _chunk: EvaluationChunk) -> GeneratedQuestion:
        return GeneratedQuestion(
            question="Why do Tempo wallets avoid exposing a private key?",
            reference_answer="They use scoped spending permissions.",
            required_facts=("Tempo wallets use scoped spending permissions.",),
            supporting_excerpt="Tempo wallets hold scoped spending permissions",
            question_type="explanation",
            difficulty="medium",
            model="generator-v2",
            input_tokens=10,
            output_tokens=5,
            question_style="explanation",
            lexical_overlap_ratio=0.2,
            supporting_chunk_count=1,
            requires_decomposition=False,
        )


class FakeNaturalizer:
    PROMPT_VERSION = "synthetic-naturalizer-v1"

    @property
    def model(self) -> str:
        return "naturalizer-model"

    def naturalize(self, question: str) -> str:
        return f"Hey, could you tell me: {question}"


def answerable_case() -> SyntheticEvaluationCase:
    return SyntheticDatasetBuilder(
        corpus=FakeCorpus(),
        generator=FakeV2Generator(),
    ).build(count=1, seed="seed")[0]


def no_answer_case() -> SyntheticEvaluationCase:
    return SyntheticEvaluationCase(
        case_id="no-answer-0001",
        dataset_version=SyntheticDatasetBuilder.DATASET_VERSION_V2,
        target_chunk_id=None,
        target_document_id=None,
        target_revision_id=None,
        content_fingerprint=None,
        question="Does the knowledge base mention the author's favorite color?",
        reference_answer="No answer exists.",
        required_facts=(),
        supporting_excerpt="",
        acceptable_chunk_ids=(),
        source_provider="substack",
        question_type="insufficient_evidence",
        difficulty="easy",
        generator_model="human",
        generator_prompt_version="human-authored-v1",
        no_answer=True,
        distractor_chunk_ids=("chunk-a",),
    )


class FakeRetrieval:
    def retrieve(self, question: str, **kwargs: object) -> RetrievalResult:
        assert question
        strategy = cast(RetrievalStrategyName, kwargs["strategy"])
        evidence = (
            Evidence(
                citation_id="E1",
                chunk_id="other",
                document_id="doc-b",
                revision_id="rev-b",
                title="Other",
                source_url="https://example.com/other",
                vault_path="Articles/other.md",
                heading_path=(),
                content="Other",
                score=1.0,
            ),
            Evidence(
                citation_id="E2",
                chunk_id="chunk-a",
                document_id="doc-a",
                revision_id="rev-a",
                title="Tempo",
                source_url="https://example.com/tempo",
                vault_path="Articles/tempo.md",
                heading_path=(),
                content="Tempo",
                score=0.9,
            ),
        )
        return RetrievalResult(
            evidence=evidence,
            trace=RetrievalTrace(
                strategy=strategy,
                route=QueryRoute.SIMPLE,
                subqueries=(question,),
                retrieval_rounds=1,
                stop_reason="complete",
            ),
        )


def runner() -> RetrievalEvaluationRunner:
    return RetrievalEvaluationRunner(
        corpus=FakeCorpus(),
        retrieval=cast(Any, FakeRetrieval()),
    )


def test_validator_rejects_source_oriented_wording() -> None:
    validator = DeterministicQuestionValidator()

    validation = validator.validate("What does the article say about Tempo wallets?", SOURCE)

    assert not validation.valid
    assert any("source-oriented" in reason for reason in validation.reasons)
    assert "the article" in validator.forbidden_source_wording("What does the article say?")


def test_validator_rejects_long_distinctive_phrase_reuse() -> None:
    validator = DeterministicQuestionValidator()
    copied = " ".join(SOURCE.split()[:12])
    question = f"I remember reading something; {copied} -- is that accurate?"

    validation = validator.validate(question, SOURCE)

    assert not validation.valid
    assert any("distinctive phrase" in reason for reason in validation.reasons)
    assert validation.longest_shared_phrase is not None


def test_validator_rejects_high_lexical_overlap() -> None:
    validator = DeterministicQuestionValidator(max_lexical_overlap=0.5)
    question = "Why do Tempo wallets use spending permissions and keep keys private?"

    validation = validator.validate(question, SOURCE)

    assert not validation.valid
    assert any("lexical overlap" in reason for reason in validation.reasons)
    assert validation.longest_shared_phrase is None


def test_validator_accepts_natural_question() -> None:
    validator = DeterministicQuestionValidator()

    validation = validator.validate("Why did the wallet ask me to reauthorize payments?", SOURCE)

    assert validation.valid
    assert validation.reasons == ()


def test_lexical_overlap_and_longest_shared_phrase_helpers() -> None:
    assert lexical_overlap_ratio("Tempo wallet", "Tempo wallet authorization") == 1.0
    assert lexical_overlap_ratio("", SOURCE) == 0.0
    assert lexical_overlap_ratio("unrelated words", SOURCE) < 0.5

    copied = " ".join(SOURCE.split()[:9])
    assert (
        longest_shared_phrase(f"hello {copied} world", SOURCE, min_tokens=8) == copied.lower()
    )
    assert longest_shared_phrase("unrelated words", SOURCE, min_tokens=8) is None


def test_v2_generator_regenerates_after_validation_failure() -> None:
    bad: dict[str, Any] = {
        "question": (
            "What does the passage say about Tempo wallets holding scoped spending "
            "permissions instead of raw private keys?"
        ),
        "reference_answer": "They hold scoped spending permissions.",
        "required_facts": ["Tempo wallets hold scoped permissions."],
        "supporting_excerpt": "Tempo wallets hold scoped spending permissions",
        "supporting_chunk_count": 1,
        "question_type": "fact",
        "difficulty": "easy",
    }
    responses = FakeV2Responses([bad, GOOD_PAYLOAD])

    generated = v2_generator(responses).generate(chunk())

    assert responses.calls == 2
    assert generated.question == GOOD_PAYLOAD["question"]
    assert generated.question_style in {"fact", "explanation", "comparison", "exact_lookup"}
    assert generated.model == "generator-v2"


def test_v2_generator_records_style_and_difficulty() -> None:
    generated = v2_generator(FakeV2Responses([GOOD_PAYLOAD])).generate(chunk())

    assert generated.question_style in {"fact", "explanation", "comparison", "exact_lookup"}
    assert generated.lexical_overlap_ratio is not None
    assert generated.lexical_overlap_ratio >= 0.0
    assert generated.supporting_chunk_count == 1
    assert generated.requires_decomposition is False


def test_v2_style_distribution_is_configurable() -> None:
    generated = v2_generator(
        FakeV2Responses([GOOD_PAYLOAD]),
        style_weights={"comparison": 1.0},
    ).generate(chunk())

    assert generated.question_style == "comparison"


def test_v2_style_distribution_is_deterministic_per_chunk() -> None:
    def style_for(chunk_id: str) -> str:
        generated = v2_generator(FakeV2Responses([GOOD_PAYLOAD])).generate(
            chunk(chunk_id=chunk_id)
        )
        assert generated.question_style is not None
        return generated.question_style

    first = style_for("chunk-a")
    second = style_for("chunk-a")

    assert first == second
    assert first in {"fact", "explanation", "comparison", "exact_lookup"}


def test_v2_generator_rejects_unknown_style_weight() -> None:
    with pytest.raises(ValueError, match="unknown question style"):
        v2_generator(FakeV2Responses([GOOD_PAYLOAD]), style_weights={"cooking": 1.0})


def test_naturalizer_rewrites_question_without_source() -> None:
    def create(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            output_text="Could you remind me why Tempo wallets keep private keys safe?"
        )

    naturalizer = OpenAISyntheticQuestionNaturalizer(
        api_key="unused",
        model="naturalizer-model",
        client=cast(Any, SimpleNamespace(responses=SimpleNamespace(create=create))),
    )

    assert naturalizer.model == "naturalizer-model"
    assert naturalizer.PROMPT_VERSION == "synthetic-naturalizer-v1"
    rewritten = naturalizer.naturalize("Why do Tempo wallets avoid exposing a private key?")
    assert "private keys" in rewritten


def test_naturalizer_retries_on_source_oriented_output() -> None:
    outputs = iter(
        ["This is what the article says about wallets.", "Why do wallets use scoped permissions?"]
    )

    def create(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(output_text=next(outputs))

    naturalizer = OpenAISyntheticQuestionNaturalizer(
        api_key="unused",
        model="naturalizer-model",
        client=cast(Any, SimpleNamespace(responses=SimpleNamespace(create=create))),
    )

    assert naturalizer.naturalize("q") == "Why do wallets use scoped permissions?"


def test_builder_v2_records_generator_and_naturalizer_versions() -> None:
    cases = SyntheticDatasetBuilder(
        corpus=FakeCorpus(),
        generator=FakeV2Generator(),
        naturalizer=FakeNaturalizer(),
        version=SyntheticDatasetBuilder.DATASET_VERSION_V2,
    ).build(count=1, seed="seed")

    case = cases[0]
    assert case.case_id.startswith("synthetic-v2-")
    assert case.dataset_version == "synthetic-chunks-v2"
    assert case.generator_prompt_version == "synthetic-question-v2"
    assert case.naturalizer_prompt_version == "synthetic-naturalizer-v1"
    assert case.naturalizer_model == "naturalizer-model"
    assert case.question_style == "explanation"
    assert case.lexical_overlap_ratio is not None
    assert case.supporting_chunk_count == 1
    assert case.requires_decomposition is False


def test_builder_v1_keeps_legacy_version() -> None:
    cases = SyntheticDatasetBuilder(
        corpus=FakeCorpus(),
        generator=FakeV2Generator(),
        version=SyntheticDatasetBuilder.DATASET_VERSION_V1,
    ).build(count=1, seed="seed")

    case = cases[0]
    assert case.case_id.startswith("synthetic-v1-")
    assert case.dataset_version == "synthetic-chunks-v1"
    assert case.naturalizer_prompt_version is None
    assert case.naturalizer_model is None


def test_builder_keeps_original_question_when_naturalization_fails() -> None:
    class FailingNaturalizer(FakeNaturalizer):
        def naturalize(self, question: str) -> str:
            return "What does the article say about wallets?"

    cases = SyntheticDatasetBuilder(
        corpus=FakeCorpus(),
        generator=FakeV2Generator(),
        naturalizer=FailingNaturalizer(),
        version=SyntheticDatasetBuilder.DATASET_VERSION_V2,
    ).build(count=1, seed="seed")

    case = cases[0]
    assert case.question == FakeV2Generator().generate(chunk()).question
    assert case.naturalizer_prompt_version is None


def test_v1_dataset_without_v2_fields_loads() -> None:
    record: dict[str, object] = {
        "case_id": "synthetic-v1-0001",
        "dataset_version": "synthetic-chunks-v1",
        "target_chunk_id": "chunk-a",
        "target_document_id": "doc-a",
        "target_revision_id": "rev-a",
        "content_fingerprint": "sha256:" + "a" * 64,
        "question": "How does RRF combine result lists?",
        "reference_answer": "It uses reciprocal rank scores.",
        "required_facts": ["RRF uses reciprocal rank scores."],
        "supporting_excerpt": "RRF combines ranked result lists using reciprocal rank scores.",
        "acceptable_chunk_ids": ["chunk-a"],
        "source_provider": "substack",
        "question_type": "fact",
        "difficulty": "easy",
        "generator_model": "generator-test",
        "generator_prompt_version": "synthetic-question-v1",
    }

    case = SyntheticEvaluationCase.from_dict(record)

    assert case.dataset_version == "synthetic-chunks-v1"
    assert case.target_chunk_id == "chunk-a"
    assert case.no_answer is False
    assert case.question_style is None
    assert case.naturalizer_prompt_version is None
    assert case.lexical_overlap_ratio is None


def test_no_answer_case_round_trips_through_json() -> None:
    loaded = SyntheticEvaluationCase.from_dict(no_answer_case().as_dict())

    assert loaded == no_answer_case()
    assert loaded.target_chunk_id is None
    assert loaded.required_facts == ()
    assert loaded.no_answer is True


def test_runner_excludes_no_answer_cases_from_hitk() -> None:
    results, summary = runner().run(
        (no_answer_case(),),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
    )

    assert summary.cases == 1
    assert summary.no_answer_cases == 1
    assert summary.hit_at_5 == 0.0
    assert summary.no_answer_false_positive_rate == 1.0
    assert results[0].no_answer is True
    assert results[0].false_positive is True


def test_runner_mixed_answerable_and_no_answer_cases() -> None:
    cases = (answerable_case(), no_answer_case())

    results, summary = runner().run(
        cases,
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
    )

    assert summary.cases == 2
    assert summary.no_answer_cases == 1
    assert summary.hit_at_5 == 1.0
    assert summary.mean_reciprocal_rank == 0.5
    answerable = next(result for result in results if not result.no_answer)
    assert answerable.target_rank == 2


def test_runner_no_answer_false_positive_false_when_distractor_absent() -> None:
    case = replace(no_answer_case(), distractor_chunk_ids=("missing-chunk",))

    results, summary = runner().run(
        (case,),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
    )

    assert results[0].false_positive is False
    assert summary.no_answer_false_positive_rate == 0.0
