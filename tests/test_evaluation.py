from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from knowledge_assistant.application.evaluation import (
    RetrievalEvaluationRunner,
    SyntheticDatasetBuilder,
    load_dataset,
    write_jsonl,
)
from knowledge_assistant.domain.evaluation import (
    EvaluationChunk,
    GeneratedQuestion,
    SyntheticEvaluationCase,
)
from knowledge_assistant.domain.query import Evidence
from knowledge_assistant.domain.retrieval import (
    QueryRoute,
    RetrievalResult,
    RetrievalStrategyName,
    RetrievalTrace,
)
from knowledge_assistant.infrastructure.openai.evaluation import (
    OpenAISyntheticQuestionGenerator,
)

GENERATION_ID = uuid.uuid4()


def chunk() -> EvaluationChunk:
    return EvaluationChunk(
        generation_id=str(GENERATION_ID),
        chunk_id="chunk-a",
        document_id="doc-a",
        revision_id="rev-a",
        content="RRF combines ranked result lists using reciprocal rank scores.",
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
        return None


class FakeGenerator:
    PROMPT_VERSION = "synthetic-question-v1"

    def generate(self, _chunk: EvaluationChunk) -> GeneratedQuestion:
        return GeneratedQuestion(
            question="How does RRF combine result lists?",
            reference_answer="It uses reciprocal rank scores.",
            required_facts=("RRF uses reciprocal rank scores.",),
            supporting_excerpt="RRF combines ranked result lists using reciprocal rank scores.",
            question_type="fact",
            difficulty="easy",
            model="generator-test",
            input_tokens=10,
            output_tokens=5,
        )


def test_dataset_builder_and_jsonl_round_trip(tmp_path: Path) -> None:
    cases = SyntheticDatasetBuilder(
        corpus=FakeCorpus(),
        generator=FakeGenerator(),
    ).build(count=1, seed="seed")
    output = tmp_path / "private" / "dataset.jsonl"
    write_jsonl(output, tuple(case.as_dict() for case in cases))

    loaded = load_dataset(output)

    assert loaded == cases
    assert loaded[0].acceptable_chunk_ids == ("chunk-a",)


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
                title="RRF",
                source_url="https://example.com/rrf",
                vault_path="Articles/rrf.md",
                heading_path=(),
                content="RRF",
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


def evaluation_case() -> SyntheticEvaluationCase:
    generated = SyntheticDatasetBuilder(
        corpus=FakeCorpus(),
        generator=FakeGenerator(),
    ).build(count=1, seed="seed")
    return generated[0]


def test_retrieval_evaluation_computes_persistent_chunk_metrics() -> None:
    results, summary = RetrievalEvaluationRunner(
        corpus=FakeCorpus(),
        retrieval=cast(Any, FakeRetrieval()),
    ).run(
        (evaluation_case(),),
        strategy=RetrievalStrategyName.RRF_HYBRID,
    )

    assert results[0].target_rank == 2
    assert results[0].reciprocal_rank == 0.5
    assert summary.hit_at_5 == 1.0
    assert summary.mean_reciprocal_rank == 0.5


class FakeQuestionResponses:
    def __init__(self, excerpt: str) -> None:
        self._excerpt = excerpt

    def parse(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            output_parsed=SimpleNamespace(
                question="How does RRF combine result lists?",
                reference_answer="With reciprocal rank scores.",
                required_facts=["It uses reciprocal rank scores."],
                supporting_excerpt=self._excerpt,
                question_type="fact",
                difficulty="easy",
            ),
            usage=SimpleNamespace(input_tokens=20, output_tokens=10),
            model="generator-model",
        )


class RetryingQuestionResponses:
    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **_kwargs: object) -> SimpleNamespace:
        self.calls += 1
        excerpt = (
            "invented excerpt"
            if self.calls == 1
            else "RRF combines ranked result lists using reciprocal rank scores."
        )
        return FakeQuestionResponses(excerpt).parse()


def test_openai_question_generator_validates_verbatim_support() -> None:
    generator = OpenAISyntheticQuestionGenerator(
        api_key="unused",
        model="generator-model",
        client=cast(
            Any,
            SimpleNamespace(
                responses=FakeQuestionResponses(
                    "RRF combines ranked result lists using reciprocal rank scores."
                )
            ),
        ),
    )

    generated = generator.generate(chunk())

    assert generated.question_type == "fact"
    assert generated.input_tokens == 20

    invalid = OpenAISyntheticQuestionGenerator(
        api_key="unused",
        model="generator-model",
        client=cast(
            Any,
            SimpleNamespace(responses=FakeQuestionResponses("invented excerpt")),
        ),
    )
    with pytest.raises(RuntimeError, match="not present"):
        invalid.generate(chunk())


def test_openai_question_generator_retries_invalid_structured_result() -> None:
    responses = RetryingQuestionResponses()
    generated = OpenAISyntheticQuestionGenerator(
        api_key="unused",
        model="generator-model",
        client=cast(Any, SimpleNamespace(responses=responses)),
    ).generate(chunk())

    assert generated.supporting_excerpt in chunk().content
    assert responses.calls == 2


def test_dataset_loader_rejects_non_object_line(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    with pytest.raises(ValueError, match="not an object"):
        load_dataset(path)


def test_dataset_builder_rejects_insufficient_sample() -> None:
    with pytest.raises(RuntimeError, match="only 1 chunks"):
        SyntheticDatasetBuilder(
            corpus=FakeCorpus(),
            generator=FakeGenerator(),
        ).build(count=2, seed="seed")

    builder = SyntheticDatasetBuilder(corpus=FakeCorpus(), generator=FakeGenerator())
    with pytest.raises(ValueError, match="count"):
        builder.build(count=0, seed="seed")
    with pytest.raises(ValueError, match="seed"):
        builder.build(count=1, seed=" ")


def test_retrieval_evaluation_rejects_invalid_datasets() -> None:
    runner = RetrievalEvaluationRunner(
        corpus=FakeCorpus(),
        retrieval=cast(Any, FakeRetrieval()),
    )
    with pytest.raises(ValueError, match="must not be empty"):
        runner.run((), strategy=RetrievalStrategyName.VECTOR_ONLY)

    first = evaluation_case()
    mixed = replace(first, dataset_version="different-version")
    with pytest.raises(ValueError, match="mixes dataset versions"):
        runner.run(
            (first, mixed),
            strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        )


def test_retrieval_evaluation_rejects_changed_target() -> None:
    class ChangedCorpus(FakeCorpus):
        def validate_chunk(self, **_kwargs: object) -> bool:
            return False

    with pytest.raises(RuntimeError, match="missing or changed chunk"):
        RetrievalEvaluationRunner(
            corpus=ChangedCorpus(),
            retrieval=cast(Any, FakeRetrieval()),
        ).run(
            (evaluation_case(),),
            strategy=RetrievalStrategyName.LEXICAL_ONLY,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("question", ""), ("required_facts", [""]), ("acceptable_chunk_ids", [""])],
)
def test_evaluation_case_rejects_invalid_required_fields(
    field: str,
    value: object,
) -> None:
    record = evaluation_case().as_dict()
    record[field] = value

    with pytest.raises(ValueError, match="requires"):
        SyntheticEvaluationCase.from_dict(record)


def test_human_labels_schema_validation(tmp_path: Path) -> None:
    from knowledge_assistant.application.evaluation import load_human_labels

    labels_path = tmp_path / "human-labels.jsonl"
    labels_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "case_id": "sample-q-01",
                        "approach": "grounded-answer-v2",
                        "factual_correctness": 4,
                        "groundedness": 3,
                        "completeness": 4,
                        "relevance_concision": 4,
                        "uncertainty": 3,
                        "overall": 4,
                    }
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )

    labels = load_human_labels(labels_path)
    assert len(labels) == 1
    assert labels[0]["case_id"] == "sample-q-01"

    labels_path.write_text(
        json.dumps({"case_id": "sample-q-02", "approach": "v2"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="missing fields"):
        load_human_labels(labels_path)

    labels_path.write_text(
        json.dumps(
            {
                "case_id": "sample-q-02",
                "approach": "v2",
                "factual_correctness": 9,
                "groundedness": 3,
                "completeness": 4,
                "relevance_concision": 4,
                "uncertainty": 3,
                "overall": 4,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"0\.\.5"):
        load_human_labels(labels_path)


def test_judge_calibration_computes_mae_bias_and_correlation() -> None:
    from knowledge_assistant.application.evaluation import calibrate_judge_scores

    def label(case_id: str, **scores: int) -> dict[str, object]:
        return {"case_id": case_id, "approach": "grounded-answer-v2", **scores}

    def judge(case_id: str, **scores: float) -> tuple[str, str, dict[str, object]]:
        return case_id, "grounded-answer-v2", {
            "model": "judge-model",
            "prompt_version": "answer-judge-prompt-v1",
            "rubric_version": "answer-judge-rubric-v1",
            **scores,
        }

    human = (
        label(
            "c1",
            factual_correctness=4,
            groundedness=3,
            completeness=4,
            relevance_concision=4,
            uncertainty=3,
            overall=4,
        ),
        label(
            "c2",
            factual_correctness=2,
            groundedness=1,
            completeness=3,
            relevance_concision=3,
            uncertainty=2,
            overall=2,
        ),
        label(
            "c3",
            factual_correctness=5,
            groundedness=4,
            completeness=5,
            relevance_concision=5,
            uncertainty=4,
            overall=5,
        ),
    )
    def scores(**values: int) -> dict[str, int]:
        return values

    judge_scores = (
        judge(
            "c1",
            **scores(
                factual_correctness=5,
                groundedness=4,
                completeness=4,
                relevance_concision=5,
                uncertainty=3,
                overall=4,
            ),
        ),
        judge(
            "c2",
            **scores(
                factual_correctness=3,
                groundedness=1,
                completeness=3,
                relevance_concision=3,
                uncertainty=2,
                overall=3,
            ),
        ),
        judge(
            "c3",
            **scores(
                factual_correctness=5,
                groundedness=5,
                completeness=5,
                relevance_concision=4,
                uncertainty=4,
                overall=4,
            ),
        ),
        judge(
            "unmatched",
            **scores(
                factual_correctness=1,
                groundedness=1,
                completeness=1,
                relevance_concision=1,
                uncertainty=1,
                overall=1,
            ),
        ),
    )

    report = calibrate_judge_scores(human, judge_scores)

    assert report.human_label_count == 3
    assert report.matched_result_count == 3
    # Judge metadata is preserved through the calibration contract.
    assert report.judge_model == "judge-model"
    assert report.judge_prompt_version == "answer-judge-prompt-v1"
    assert report.judge_rubric_version == "answer-judge-rubric-v1"
    factual = next(d for d in report.dimensions if d.name == "factual_correctness")
    assert factual.mean_absolute_error == pytest.approx((1 + 1 + 0) / 3)
    assert factual.bias == pytest.approx((1 + 1 + 0) / 3)
    assert factual.pearson is not None
    assert factual.pearson > 0.8
    groundedness = next(d for d in report.dimensions if d.name == "groundedness")
    assert groundedness.bias == pytest.approx(2 / 3)


def test_judge_calibration_without_human_labels_is_not_run() -> None:
    from knowledge_assistant.application.evaluation import calibrate_judge_scores

    report = calibrate_judge_scores((), ())
    assert report.human_label_count == 0
    assert report.matched_result_count == 0
    assert all(dimension.mean_absolute_error is None for dimension in report.dimensions)
