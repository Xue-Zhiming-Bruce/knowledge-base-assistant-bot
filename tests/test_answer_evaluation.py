"""End-to-end answer evaluation runner, judge, and public-safe summary tests."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from knowledge_assistant.application.evaluation import (
    AnswerEvaluationRunner,
    SyntheticDatasetBuilder,
    render_answer_evaluation_markdown,
    write_jsonl,
)
from knowledge_assistant.domain.evaluation import (
    AnswerJudgeResult,
    EvaluationChunk,
    GeneratedQuestion,
    SyntheticEvaluationCase,
)
from knowledge_assistant.domain.query import (
    ContextPolicy,
    Evidence,
    GeneratedAnswer,
    bound_evidence,
)
from knowledge_assistant.domain.retrieval import (
    QueryRoute,
    RetrievalResult,
    RetrievalStrategyName,
    RetrievalTrace,
)
from knowledge_assistant.infrastructure.openai.answers import (
    OpenAIAnswerGenerator,
    OpenAIAnswerGeneratorV2,
)
from knowledge_assistant.infrastructure.openai.evaluation import OpenAIAnswerJudge

GENERATION_ID = uuid.uuid4()

SOURCE = (
    "Tempo wallets hold scoped spending permissions instead of raw private keys. "
    "A Tempo wallet can authorize a single request with a maximum spend cap, so "
    "the user never exposes a wallet private key to a third-party service."
)

GOOD_ANSWER = (
    "Tempo wallets use scoped spending permissions [E2]. "
    "They avoid exposing private keys [E2]."
)


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


class FakeAnswerGenerator:
    PROMPT_VERSION = "grounded-answer-v1"

    def __init__(
        self,
        *,
        answer: str = GOOD_ANSWER,
        citation_ids: tuple[str, ...] = ("E2",),
        sufficient_evidence: bool = True,
        model: str = "gen-model",
    ) -> None:
        self._answer = answer
        self._citation_ids = citation_ids
        self._sufficient_evidence = sufficient_evidence
        self._model = model

    def generate(
        self,
        *,
        question: str,
        history: tuple[object, ...],
        evidence: tuple[Evidence, ...],
    ) -> GeneratedAnswer:
        del history
        assert question
        assert evidence
        return GeneratedAnswer(
            answer=self._answer,
            citation_ids=self._citation_ids,
            sufficient_evidence=self._sufficient_evidence,
            model=self._model,
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
                chunk_id="other",
                document_id="doc-b",
                revision_id="rev-b",
                title="Other",
                source_url="https://example.com/other",
                vault_path="Articles/other.md",
                heading_path=(),
                content="Unrelated content about an entirely different topic.",
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
                content=SOURCE,
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


def make_runner(
    *,
    judge: object | None = None,
    generators: dict[str, FakeAnswerGenerator] | None = None,
) -> AnswerEvaluationRunner:
    selected = generators or {
        "grounded-answer-v1": FakeAnswerGenerator(),
        "grounded-answer-v2": FakeAnswerGenerator(answer=GOOD_ANSWER),
    }
    return AnswerEvaluationRunner(
        corpus=FakeCorpus(),
        retrieval=cast(Any, FakeRetrieval()),
        generators=cast(Any, selected),
        context_policies={
            "grounded-answer-v1": ContextPolicy(total_limit=16_000, per_item_limit=2_400),
            "grounded-answer-v2": ContextPolicy(total_limit=12_000, per_item_limit=1_600),
        },
        judge=cast(Any, judge),
    )


class FakeJudge:
    PROMPT_VERSION = "answer-judge-prompt-v1"
    RUBRIC_VERSION = "answer-judge-rubric-v1"

    @property
    def model(self) -> str:
        return "judge-test"

    def judge(self, **_kwargs: object) -> AnswerJudgeResult:
        return AnswerJudgeResult(
            model="judge-test",
            prompt_version=self.PROMPT_VERSION,
            rubric_version=self.RUBRIC_VERSION,
            factual_correctness=4,
            groundedness=4,
            completeness=3,
            relevance_concision=4,
            uncertainty=5,
            overall=4,
            required_fact_support=(("Tempo wallets use scoped spending permissions.", True),),
            justification="solid answer",
        )


class FailingJudge(FakeJudge):
    def judge(self, **_kwargs: object) -> AnswerJudgeResult:
        raise RuntimeError("judge exploded")


def test_answer_runner_computes_deterministic_metrics() -> None:
    results, summaries = make_runner().run(
        (answerable_case(),),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )

    result = results[0]
    assert result.resolved_citations == ("E2",)
    assert result.citation_error is None
    assert result.citation_coverage == 1.0
    assert result.required_fact_lexical_coverage == 1.0
    assert result.abstained_correctly is None
    assert result.unexpected_abstention is False
    assert result.generation_model == "gen-model"
    assert result.generation_input_tokens == 40
    assert result.total_latency_seconds >= 0.0

    summary = summaries[0]
    assert summary.approach == "grounded-answer-v1"
    assert summary.cases == 1
    assert summary.citation_validity_rate == 1.0
    assert summary.citation_coverage_mean == 1.0
    assert summary.no_answer_cases == 0
    assert summary.judge_applied is False


def test_answer_runner_records_citation_error() -> None:
    results, summaries = make_runner(
        generators={"grounded-answer-v1": FakeAnswerGenerator(citation_ids=("E9",))}
    ).run(
        (answerable_case(),),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )

    assert results[0].citation_error is not None
    assert "unknown evidence" in results[0].citation_error or results[0].citation_error
    assert results[0].resolved_citations == ()
    assert summaries[0].citation_validity_rate == 0.0


def test_answer_runner_no_answer_abstention_and_false_positive() -> None:
    abstaining = make_runner(
        generators={
            "grounded-answer-v1": FakeAnswerGenerator(
                answer="I don't have enough information to answer that.",
                citation_ids=(),
                sufficient_evidence=False,
            )
        }
    )
    results, summaries = abstaining.run(
        (no_answer_case(),),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )
    assert results[0].abstained_correctly is True
    assert summaries[0].no_answer_abstention_rate == 1.0
    assert summaries[0].no_answer_cases == 1

    overconfident = make_runner(
        generators={"grounded-answer-v1": FakeAnswerGenerator()}
    )
    results, summaries = overconfident.run(
        (no_answer_case(),),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )
    assert results[0].abstained_correctly is False
    assert summaries[0].no_answer_abstention_rate == 0.0


def test_answer_runner_records_unexpected_abstention() -> None:
    results, summaries = make_runner(
        generators={
            "grounded-answer-v1": FakeAnswerGenerator(
                answer="I don't have enough information.",
                citation_ids=(),
                sufficient_evidence=False,
            )
        }
    ).run(
        (answerable_case(),),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )

    assert results[0].unexpected_abstention is True
    assert summaries[0].unexpected_abstention_rate == 1.0


def test_answer_runner_records_judge_scores() -> None:
    results, summaries = make_runner(judge=FakeJudge()).run(
        (answerable_case(),),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )

    result = results[0]
    assert result.judge is not None
    assert result.judge.model == "judge-test"
    assert result.judge.rubric_version == "answer-judge-rubric-v1"
    assert result.judge.factual_correctness == 4
    assert result.judge.required_fact_support[0][1] is True
    assert result.judge_error is None

    summary = summaries[0]
    assert summary.judge_applied is True
    assert summary.judge_model == "judge-test"
    assert summary.mean_overall == 4.0
    assert summary.mean_uncertainty == 5.0


def test_answer_runner_records_judge_failure_without_aborting() -> None:
    results, summaries = make_runner(judge=FailingJudge()).run(
        (answerable_case(),),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )

    assert results[0].judge is None
    assert results[0].judge_error is not None
    assert "judge exploded" in results[0].judge_error or results[0].judge_error
    assert summaries[0].judge_applied is False


def test_answer_runner_rejects_unknown_approach() -> None:
    runner = make_runner()
    with pytest.raises(ValueError, match="unknown answer approaches"):
        runner.run(
            (answerable_case(),),
            strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
            approaches=("not-an-approach",),
        )


def test_answer_runner_requires_context_policies_for_generators() -> None:
    with pytest.raises(ValueError, match="missing context policies"):
        AnswerEvaluationRunner(
            corpus=FakeCorpus(),
            retrieval=cast(Any, FakeRetrieval()),
            generators=cast(
                Any,
                {
                    "grounded-answer-v1": FakeAnswerGenerator(),
                    "grounded-answer-v2": FakeAnswerGenerator(),
                },
            ),
            context_policies={
                "grounded-answer-v1": ContextPolicy(total_limit=16_000, per_item_limit=2_400)
            },
        )






def test_summary_breakdowns_by_type_and_difficulty() -> None:
    fact_case = replace(
        answerable_case(),
        case_id="case-fact",
        question_type="fact",
        difficulty="easy",
    )
    comparison_case = replace(
        answerable_case(),
        case_id="case-compare",
        question_type="comparison",
        difficulty="hard",
    )
    _results, summaries = make_runner().run(
        (fact_case, comparison_case),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )

    summary = summaries[0]
    assert summary.cases == 2
    by_type = {breakdown.label: breakdown for breakdown in summary.by_question_type}
    assert by_type["fact"].cases == 1
    assert by_type["comparison"].cases == 1
    by_difficulty = {breakdown.label: breakdown for breakdown in summary.by_difficulty}
    assert by_difficulty["easy"].cases == 1
    assert by_difficulty["hard"].cases == 1
    assert by_type["fact"].citation_validity_rate == 1.0


def test_render_markdown_is_public_safe() -> None:
    _results, summaries = make_runner(judge=FakeJudge()).run(
        (answerable_case(), no_answer_case()),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
    )
    markdown = render_answer_evaluation_markdown(summaries)

    assert "grounded-answer-v1" in markdown
    assert "grounded-answer-v2" in markdown
    assert "answer-judge-rubric-v1" in markdown
    assert "public-safe" in markdown
    assert "calibrat" in markdown
    assert "Why do Tempo wallets avoid exposing a private key?" not in markdown
    assert "scoped spending permissions [E2]" not in markdown
    assert SOURCE not in markdown


def test_answer_evaluation_jsonl_round_trip(tmp_path: Path) -> None:
    results, _summaries = make_runner(judge=FakeJudge()).run(
        (answerable_case(),),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
        approaches=("grounded-answer-v1",),
    )
    output = tmp_path / "answer-results.jsonl"
    write_jsonl(output, tuple(result.as_dict() for result in results))

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["approach"] == "grounded-answer-v1"
    assert record["judge"]["rubric_version"] == "answer-judge-rubric-v1"
    assert record["answer"] == GOOD_ANSWER


def test_bound_evidence_respects_context_policy() -> None:
    long_item = Evidence(
        citation_id="E1",
        chunk_id="chunk-a",
        document_id="doc-a",
        revision_id="rev-a",
        title="Tempo",
        source_url="https://example.com/tempo",
        vault_path="Articles/tempo.md",
        heading_path=(),
        content="word " * 500,
        score=1.0,
    )
    bounded = bound_evidence(
        (long_item,),
        policy=ContextPolicy(total_limit=100, per_item_limit=80),
    )

    assert len(bounded) == 1
    assert len(bounded[0].content) == 80
    assert bound_evidence((), policy=ContextPolicy(total_limit=100, per_item_limit=80)) == ()




def test_grounded_answer_v2_differs_meaningfully_from_v1() -> None:
    assert OpenAIAnswerGenerator.PROMPT_VERSION == "grounded-answer-v1"
    assert OpenAIAnswerGeneratorV2.PROMPT_VERSION == "grounded-answer-v2"
    v1_prompt = OpenAIAnswerGenerator._system_prompt()
    v2_prompt = OpenAIAnswerGeneratorV2._system_prompt()
    assert v1_prompt != v2_prompt
    assert "End EVERY sentence" in v2_prompt
    assert "End EVERY sentence" not in v1_prompt
    assert "sufficient_evidence=false" in v2_prompt


class FakeJudgeResponses:
    def __init__(self, scores: dict[str, int]) -> None:
        self._scores = scores

    def parse(self, **kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            output_parsed=SimpleNamespace(
                **self._scores,
                required_fact_support=[
                    SimpleNamespace(
                        fact="Tempo wallets use scoped spending permissions.", supported=True
                    )
                ],
                justification="good",
            ),
            usage=SimpleNamespace(input_tokens=50, output_tokens=30),
            model="judge-model",
        )


def test_openai_answer_judge_parses_structured_output() -> None:
    judge = OpenAIAnswerJudge(
        api_key="unused",
        model="judge-model",
        client=cast(
            Any,
            SimpleNamespace(
                responses=FakeJudgeResponses(
                    {
                        "factual_correctness": 4,
                        "groundedness": 3,
                        "completeness": 4,
                        "relevance_concision": 5,
                        "uncertainty": 4,
                        "overall": 4,
                    }
                )
            ),
        ),
    )

    result = judge.judge(
        question="q",
        answer="a",
        reference_answer="r",
        required_facts=("fact one",),
        supporting_excerpt="excerpt",
        no_answer=False,
    )

    assert judge.model == "judge-model"
    assert result.rubric_version == "answer-judge-rubric-v1"
    assert result.factual_correctness == 4
    assert result.overall == 4
    assert result.required_fact_support == (
        ("Tempo wallets use scoped spending permissions.", True),
    )


def test_openai_answer_judge_rejects_out_of_range_scores() -> None:
    judge = OpenAIAnswerJudge(
        api_key="unused",
        model="judge-model",
        client=cast(
            Any,
            SimpleNamespace(
                responses=FakeJudgeResponses(
                    {
                        "factual_correctness": 6,
                        "groundedness": 3,
                        "completeness": 4,
                        "relevance_concision": 5,
                        "uncertainty": 4,
                        "overall": 4,
                    }
                )
            ),
        ),
    )

    with pytest.raises(RuntimeError, match=r"outside 0\.\.5"):
        judge.judge(
            question="q",
            answer="a",
            reference_answer="r",
            required_facts=(),
            supporting_excerpt="",
            no_answer=False,
        )


def test_answer_generator_v2_parses_structured_answer() -> None:
    class FakeResponses:
        def parse(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                output_parsed=SimpleNamespace(
                    answer="Tempo wallets use scoped permissions [E1].",
                    citation_ids=["E1"],
                    sufficient_evidence=True,
                ),
                usage=SimpleNamespace(input_tokens=30, output_tokens=12),
                model="gen-v2",
            )

    generator = OpenAIAnswerGeneratorV2(
        api_key="unused",
        model="gen-v2",
        client=cast(Any, SimpleNamespace(responses=FakeResponses())),
    )

    generated = generator.generate(
        question="q",
        history=(),
        evidence=(
            Evidence(
                citation_id="E1",
                chunk_id="c",
                document_id="d",
                revision_id="r",
                title="t",
                source_url="https://example.com",
                vault_path="p.md",
                heading_path=(),
                content="content",
                score=1.0,
            ),
        ),
    )

    assert generated.model == "gen-v2"
    assert generated.sufficient_evidence is True
    assert generated.citation_ids == ("E1",)
