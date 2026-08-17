"""Answer-evaluation target validation: chunk-level vs document-level routing."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any, cast

import pytest

from knowledge_assistant.application.evaluation import (
    AnswerEvaluationRunner,
    SyntheticDatasetBuilder,
)
from knowledge_assistant.application.evaluation_targets import validate_answer_target
from knowledge_assistant.domain.evaluation import (
    EvaluationChunk,
    GeneratedQuestion,
    SyntheticEvaluationCase,
)
from knowledge_assistant.domain.query import (
    ContextPolicy,
    Evidence,
    GeneratedAnswer,
)
from knowledge_assistant.domain.retrieval import (
    QueryRoute,
    RetrievalResult,
    RetrievalStrategyName,
    RetrievalTrace,
)

GENERATION_ID = uuid.uuid4()


def chunk() -> EvaluationChunk:
    return EvaluationChunk(
        generation_id=str(GENERATION_ID),
        chunk_id="chunk-a",
        document_id="doc-a",
        revision_id="rev-a",
        content="Tempo wallets hold scoped spending permissions.",
        content_fingerprint="sha256:" + "a" * 64,
        token_count=100,
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
        return "doc-sample"


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


def answerable_case() -> SyntheticEvaluationCase:
    return SyntheticDatasetBuilder(
        corpus=FakeCorpus(),
        generator=FakeV2Generator(),
    ).build(count=1, seed="seed")[0]


class FakeAnswerGenerator:
    PROMPT_VERSION = "grounded-answer-v1"

    def generate(self, **_kwargs: object) -> GeneratedAnswer:
        return GeneratedAnswer(
            answer="Grounded [E1].",
            citation_ids=("E1",),
            sufficient_evidence=True,
            model="gen-model",
            input_tokens=40,
            output_tokens=20,
        )


class FakeRetrieval:
    def retrieve(self, question: str, **kwargs: object) -> RetrievalResult:
        assert question
        strategy = cast(RetrievalStrategyName, kwargs["strategy"])
        evidence = (
            Evidence(
                citation_id="E1",
                chunk_id="chunk-a",
                document_id="doc-a",
                revision_id="rev-a",
                title="Tempo",
                source_url="https://example.com/tempo",
                vault_path="p.md",
                heading_path=(),
                content="content",
                score=1.0,
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


def make_runner(corpus: FakeCorpus) -> AnswerEvaluationRunner:
    return AnswerEvaluationRunner(
        corpus=cast(Any, corpus),
        retrieval=cast(Any, FakeRetrieval()),
        generators={"grounded-answer-v1": FakeAnswerGenerator()},
        context_policies={
            "grounded-answer-v1": ContextPolicy(total_limit=16_000, per_item_limit=2_400)
        },
    )


def test_chunk_level_validation_uses_fingerprint() -> None:
    case = answerable_case()
    assert case.target_chunk_id is not None
    assert case.content_fingerprint is not None

    class RecordingCorpus(FakeCorpus):
        def __init__(self) -> None:
            self.validate_calls: list[dict[str, object]] = []

        def validate_chunk(self, **kwargs: object) -> bool:
            self.validate_calls.append(kwargs)
            return True

        def document_id_for_url(self, **_kwargs: object) -> str | None:
            raise AssertionError("chunk-level cases must not use URL validation")

    corpus = RecordingCorpus()
    make_runner(corpus).run(
        (case,),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )

    assert len(corpus.validate_calls) == 1
    assert corpus.validate_calls[0]["chunk_id"] == case.target_chunk_id
    assert corpus.validate_calls[0]["content_fingerprint"] == case.content_fingerprint


def test_changed_fingerprint_fails_closed() -> None:
    case = answerable_case()

    class ChangedCorpus(FakeCorpus):
        def validate_chunk(self, **_kwargs: object) -> bool:
            return False

    with pytest.raises(RuntimeError, match="missing or changed chunk"):
        make_runner(ChangedCorpus()).run(
            (case,),
            strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
            approaches=("grounded-answer-v1",),
        )


def test_target_chunk_takes_precedence_over_document_flag() -> None:
    # A case carrying both a target chunk and document-level metadata must use
    # chunk-level fingerprint validation, not URL validation.
    case = replace(
        answerable_case(),
        document_level=True,
        target_url="https://example.com/doc",
    )

    class ChunkOnlyCorpus(FakeCorpus):
        def validate_chunk(self, **_kwargs: object) -> bool:
            return True

        def document_id_for_url(self, **_kwargs: object) -> str | None:
            raise AssertionError(
                "document URL validation must not run when a target chunk exists"
            )

    results, summaries = make_runner(ChunkOnlyCorpus()).run(
        (case,),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )

    assert len(results) == 1
    assert summaries[0].cases == 1


def test_document_level_fallback_validates_ingested_document() -> None:
    case = replace(
        answerable_case(),
        document_level=True,
        target_url="https://example.com/doc",
        target_chunk_id=None,
        content_fingerprint=None,
    )

    class IngestedCorpus(FakeCorpus):
        def document_id_for_url(self, **_kwargs: object) -> str | None:
            return "doc-sample"

        def validate_chunk(self, **_kwargs: object) -> bool:
            raise AssertionError("no target chunk, so validate_chunk must not run")

    results, summaries = make_runner(IngestedCorpus()).run(
        (case,),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )

    assert len(results) == 1
    assert summaries[0].cases == 1


def test_document_level_fallback_rejects_uningested_document() -> None:
    case = replace(
        answerable_case(),
        document_level=True,
        target_url="https://example.com/not-ingested",
        target_chunk_id=None,
        content_fingerprint=None,
    )

    class EmptyCorpus(FakeCorpus):
        def document_id_for_url(self, **_kwargs: object) -> str | None:
            return None

    with pytest.raises(RuntimeError, match="target document is not ingested"):
        make_runner(EmptyCorpus()).run(
            (case,),
            strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
            approaches=("grounded-answer-v1",),
        )


def test_case_without_any_target_fails_closed() -> None:
    broken = SyntheticEvaluationCase(
        case_id="broken",
        dataset_version=SyntheticDatasetBuilder.DATASET_VERSION_V2,
        target_chunk_id=None,
        target_document_id=None,
        target_revision_id=None,
        content_fingerprint=None,
        question="Where is the evidence?",
        reference_answer="No answer exists.",
        required_facts=(),
        supporting_excerpt="",
        acceptable_chunk_ids=(),
        source_provider="substack",
        question_type="fact",
        difficulty="easy",
        generator_model="human",
        generator_prompt_version="human-authored-v1",
        no_answer=False,
    )
    with pytest.raises(ValueError, match="has neither a target chunk nor"):
        make_runner(FakeCorpus()).run(
            (broken,),
            strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
            approaches=("grounded-answer-v1",),
        )


def test_validate_answer_target_function_contract() -> None:
    # The extracted routing function itself must expose the exact rule.
    case = answerable_case()
    validate_answer_target(FakeCorpus(), case, GENERATION_ID)  # chunk path, no raise

    doc_case = replace(
        case,
        document_level=True,
        target_url="https://example.com/doc",
        target_chunk_id=None,
        content_fingerprint=None,
    )
    validate_answer_target(FakeCorpus(), doc_case, GENERATION_ID)  # fallback, no raise

    with pytest.raises(ValueError, match="has neither a target chunk nor"):
        validate_answer_target(FakeCorpus(), broken_case(), GENERATION_ID)


def broken_case() -> SyntheticEvaluationCase:
    return SyntheticEvaluationCase(
        case_id="broken",
        dataset_version=SyntheticDatasetBuilder.DATASET_VERSION_V2,
        target_chunk_id=None,
        target_document_id=None,
        target_revision_id=None,
        content_fingerprint=None,
        question="q",
        reference_answer="a",
        required_facts=(),
        supporting_excerpt="",
        acceptable_chunk_ids=(),
        source_provider="substack",
        question_type="fact",
        difficulty="easy",
        generator_model="human",
        generator_prompt_version="human-authored-v1",
        no_answer=False,
    )
