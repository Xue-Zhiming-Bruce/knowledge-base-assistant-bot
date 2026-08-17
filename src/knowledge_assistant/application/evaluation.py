"""Synthetic dataset construction and paired retrieval and answer evaluation."""

from __future__ import annotations

import json
import os
import re
import statistics
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import UUID

from knowledge_assistant.application.evaluation_targets import validate_answer_target
from knowledge_assistant.application.retrieval import RetrievalOrchestrator
from knowledge_assistant.domain.evaluation import (
    AnswerEvaluationResult,
    AnswerEvaluationSummary,
    AnswerJudgeResult,
    AnswerSliceBreakdown,
    DeterministicQuestionValidator,
    RetrievalEvaluationResult,
    RetrievalEvaluationSummary,
    RetrievalSliceBreakdown,
    SampleCase,
    SampleManifest,
    SampleSource,
    SyntheticEvaluationCase,
    measure_difficulty,
    tokenize,
)
from knowledge_assistant.domain.query import (
    AnswerValidationError,
    CitationValidator,
    ContextPolicy,
    bound_evidence,
    extract_citation_markers,
)
from knowledge_assistant.domain.retrieval import RetrievalStrategyName
from knowledge_assistant.ports.answers import AnswerGenerator
from knowledge_assistant.ports.evaluation import (
    AnswerJudge,
    EvaluationCorpus,
    QuestionNaturalizer,
    SyntheticQuestionGenerator,
)
from knowledge_assistant.ports.telemetry import NoOpTelemetry, Telemetry


class SyntheticDatasetBuilder:
    DATASET_VERSION_V1 = "synthetic-chunks-v1"
    DATASET_VERSION_V2 = "synthetic-chunks-v2"

    def __init__(
        self,
        *,
        corpus: EvaluationCorpus,
        generator: SyntheticQuestionGenerator,
        naturalizer: QuestionNaturalizer | None = None,
        version: str = DATASET_VERSION_V2,
        telemetry: Telemetry | None = None,
    ) -> None:
        if version not in (self.DATASET_VERSION_V1, self.DATASET_VERSION_V2):
            raise ValueError(f"unsupported evaluation dataset version: {version}")
        self._corpus = corpus
        self._generator = generator
        self._naturalizer = naturalizer
        self._version = version
        self._telemetry = telemetry or NoOpTelemetry()

    def build(self, *, count: int, seed: str) -> tuple[SyntheticEvaluationCase, ...]:
        if count < 1:
            raise ValueError("evaluation case count must be positive")
        if not seed.strip():
            raise ValueError("evaluation seed must not be blank")
        generation_id = self._corpus.active_generation_id()
        chunks = self._corpus.sample_chunks(
            generation_id=generation_id,
            count=count,
            seed=seed,
        )
        if len(chunks) < count:
            raise RuntimeError(f"only {len(chunks)} chunks satisfy the evaluation sampling policy")
        cases: list[SyntheticEvaluationCase] = []
        prefix = (
            "synthetic-v1" if self._version == self.DATASET_VERSION_V1 else "synthetic-v2"
        )
        validator = DeterministicQuestionValidator()
        with self._telemetry.span(
            "evaluation.generate_dataset",
            {
                "evaluation.case_count": count,
                "evaluation.dataset_version": self._version,
            },
        ):
            for index, chunk in enumerate(chunks, start=1):
                generated = self._generator.generate(chunk)
                naturalizer_model: str | None = None
                naturalizer_prompt_version: str | None = None
                question = generated.question
                if self._naturalizer is not None:
                    try:
                        rewritten = self._naturalizer.naturalize(question)
                        if validator.validate(rewritten, chunk.content).valid:
                            question = rewritten
                            naturalizer_model = self._naturalizer.model
                            naturalizer_prompt_version = self._naturalizer.PROMPT_VERSION
                    except RuntimeError:
                        self._telemetry.count("evaluation.naturalize_failures")
                properties = measure_difficulty(
                    question=question,
                    source=chunk.content,
                    required_facts=generated.required_facts,
                    question_type=generated.question_type,
                    supporting_chunk_count=generated.supporting_chunk_count or 1,
                )
                cases.append(
                    SyntheticEvaluationCase(
                        case_id=f"{prefix}-{index:04d}",
                        dataset_version=self._version,
                        target_chunk_id=chunk.chunk_id,
                        target_document_id=chunk.document_id,
                        target_revision_id=chunk.revision_id,
                        content_fingerprint=chunk.content_fingerprint,
                        question=question,
                        reference_answer=generated.reference_answer,
                        required_facts=generated.required_facts,
                        supporting_excerpt=generated.supporting_excerpt,
                        acceptable_chunk_ids=(chunk.chunk_id,),
                        source_provider=chunk.source_provider,
                        question_type=generated.question_type,
                        difficulty=generated.difficulty,
                        generator_model=generated.model,
                        generator_prompt_version=self._generator.PROMPT_VERSION,
                        question_style=generated.question_style,
                        naturalizer_model=naturalizer_model,
                        naturalizer_prompt_version=naturalizer_prompt_version,
                        lexical_overlap_ratio=properties.lexical_overlap_ratio,
                        supporting_chunk_count=properties.supporting_chunk_count,
                        requires_decomposition=properties.requires_decomposition,
                    )
                )
        return tuple(cases)


class RetrievalEvaluationRunner:
    def __init__(
        self,
        *,
        corpus: EvaluationCorpus,
        retrieval: RetrievalOrchestrator,
        telemetry: Telemetry | None = None,
    ) -> None:
        self._corpus = corpus
        self._retrieval = retrieval
        self._telemetry = telemetry or NoOpTelemetry()

    def run(
        self,
        cases: tuple[SyntheticEvaluationCase, ...],
        *,
        strategy: RetrievalStrategyName,
        generation_id: UUID | None = None,
    ) -> tuple[tuple[RetrievalEvaluationResult, ...], RetrievalEvaluationSummary]:
        if not cases:
            raise ValueError("evaluation dataset must not be empty")
        selected_generation = generation_id or self._corpus.active_generation_id()
        dataset_versions = {case.dataset_version for case in cases}
        if len(dataset_versions) != 1:
            raise ValueError("evaluation file mixes dataset versions")
        results: list[RetrievalEvaluationResult] = []
        with self._telemetry.span(
            "evaluation.run",
            {
                "evaluation.case_count": len(cases),
                "retrieval.version": strategy.value,
            },
        ):
            for case in cases:
                if case.no_answer:
                    started = perf_counter()
                    retrieval = self._retrieval.retrieve(
                        case.question,
                        strategy=strategy,
                        generation_id=selected_generation,
                    )
                    latency = perf_counter() - started
                    ordered_ids = tuple(item.chunk_id for item in retrieval.evidence)
                    plausible = set(case.distractor_chunk_ids) | set(
                        case.acceptable_chunk_ids
                    )
                    if case.document_level and case.target_url is not None:
                        plausible |= self._resolve_document_chunks(
                            case, selected_generation
                        )
                    elif case.target_document_id is not None:
                        plausible |= set(
                            self._corpus.document_chunks(
                                generation_id=selected_generation,
                                document_id=case.target_document_id,
                            )
                        )
                    results.append(
                        RetrievalEvaluationResult(
                            case_id=case.case_id,
                            strategy=strategy.value,
                            target_rank=None,
                            hit_at_5=False,
                            hit_at_20=False,
                            reciprocal_rank=0.0,
                            retrieved_chunk_ids=ordered_ids,
                            route=retrieval.trace.route.value,
                            subqueries=retrieval.trace.subqueries,
                            retrieval_rounds=retrieval.trace.retrieval_rounds,
                            stop_reason=retrieval.trace.stop_reason,
                            latency_seconds=latency,
                            no_answer=True,
                            false_positive=bool(plausible.intersection(ordered_ids)),
                            planner_calls=(
                                1 if retrieval.trace.planner_model is not None else 0
                            ),
                            planner_input_tokens=retrieval.trace.planner_input_tokens,
                            planner_output_tokens=retrieval.trace.planner_output_tokens,
                            case_type=case.question_type,
                            case_difficulty=case.difficulty,
                        )
                    )
                    continue
                if case.document_level:
                    acceptable = self._resolve_document_chunks(case, selected_generation)
                else:
                    if case.target_chunk_id is None or case.content_fingerprint is None:
                        raise ValueError(
                            f"evaluation case {case.case_id} is missing a target chunk"
                        )
                    if not self._corpus.validate_chunk(
                        generation_id=selected_generation,
                        chunk_id=case.target_chunk_id,
                        content_fingerprint=case.content_fingerprint,
                    ):
                        raise RuntimeError(
                            f"evaluation case {case.case_id} references a missing or changed chunk"
                        )
                    acceptable = set(case.acceptable_chunk_ids)
                started = perf_counter()
                retrieval = self._retrieval.retrieve(
                    case.question,
                    strategy=strategy,
                    generation_id=selected_generation,
                )
                latency = perf_counter() - started
                ordered_ids = tuple(item.chunk_id for item in retrieval.evidence)
                rank = next(
                    (
                        index
                        for index, chunk_id in enumerate(ordered_ids, start=1)
                        if chunk_id in acceptable
                    ),
                    None,
                )
                results.append(
                    RetrievalEvaluationResult(
                        case_id=case.case_id,
                        strategy=strategy.value,
                        target_rank=rank,
                        hit_at_5=rank is not None and rank <= 5,
                        hit_at_20=rank is not None and rank <= 20,
                        reciprocal_rank=0.0 if rank is None else 1.0 / rank,
                        retrieved_chunk_ids=ordered_ids,
                        route=retrieval.trace.route.value,
                        subqueries=retrieval.trace.subqueries,
                        retrieval_rounds=retrieval.trace.retrieval_rounds,
                        stop_reason=retrieval.trace.stop_reason,
                        latency_seconds=latency,
                        planner_calls=(
                            1 if retrieval.trace.planner_model is not None else 0
                        ),
                        planner_input_tokens=retrieval.trace.planner_input_tokens,
                        planner_output_tokens=retrieval.trace.planner_output_tokens,
                        case_type=case.question_type,
                        case_difficulty=case.difficulty,
                    )
                )
        total = len(results)
        answerable = tuple(result for result in results if not result.no_answer)
        no_answer_results = tuple(result for result in results if result.no_answer)
        answerable_total = len(answerable)
        summary = RetrievalEvaluationSummary(
            dataset_version=next(iter(dataset_versions)),
            strategy=strategy.value,
            cases=total,
            hit_at_5=sum(result.hit_at_5 for result in answerable) / answerable_total
            if answerable_total
            else 0.0,
            hit_at_20=sum(result.hit_at_20 for result in answerable) / answerable_total
            if answerable_total
            else 0.0,
            mean_reciprocal_rank=(
                sum(result.reciprocal_rank for result in answerable) / answerable_total
                if answerable_total
                else 0.0
            ),
            mean_latency_seconds=sum(result.latency_seconds for result in results) / total,
            no_answer_cases=len(no_answer_results),
            no_answer_false_positive_rate=(
                sum(1 for result in no_answer_results if result.false_positive)
                / len(no_answer_results)
                if no_answer_results
                else None
            ),
            mean_planner_calls=sum(result.planner_calls for result in results) / total
            if total
            else 0.0,
            mean_planner_input_tokens=(
                sum(float(result.planner_input_tokens or 0) for result in results) / total
                if total
                else 0.0
            ),
            mean_planner_output_tokens=(
                sum(float(result.planner_output_tokens or 0) for result in results) / total
                if total
                else 0.0
            ),
            by_question_type=_retrieval_slices(
                tuple(results), key=lambda result: result.case_type
            ),
            by_difficulty=_retrieval_slices(
                tuple(results), key=lambda result: result.case_difficulty
            ),
        )
        return tuple(results), summary

    def _resolve_document_chunks(
        self,
        case: SyntheticEvaluationCase,
        generation_id: UUID,
    ) -> set[str]:
        if case.target_url is None:
            raise ValueError(
                f"evaluation case {case.case_id} requires a target document URL "
                "for document-level evaluation"
            )
        document_id = self._corpus.document_id_for_url(url=case.target_url)
        if document_id is None:
            raise RuntimeError(
                f"evaluation case {case.case_id} target document is not ingested: "
                f"{case.target_url}"
            )
        return set(
            self._corpus.document_chunks(
                generation_id=generation_id,
                document_id=document_id,
            )
        )


def load_dataset(path: Path) -> tuple[SyntheticEvaluationCase, ...]:
    cases: list[SyntheticEvaluationCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"evaluation line {line_number} is not an object")
        cases.append(SyntheticEvaluationCase.from_dict(value))
    return tuple(cases)


def write_jsonl(path: Path, records: tuple[dict[str, object], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            for record in records:
                output.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "would",
    }
)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _material_sentences(text: str) -> tuple[str, ...]:
    sentences = tuple(part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip())
    return tuple(sentence for sentence in sentences if len(tokenize(sentence)) > 3)


def _citation_coverage(text: str) -> float:
    """Fraction of material sentences that carry at least one citation marker."""

    sentences = _material_sentences(text)
    if not sentences:
        return 0.0
    marked = sum(1 for sentence in sentences if extract_citation_markers(sentence))
    return marked / len(sentences)


def _required_fact_lexical_coverage(answer: str, required_facts: tuple[str, ...]) -> float:
    """Deterministic proxy: mean fraction of non-stopword fact tokens in the answer."""

    if not required_facts:
        return 1.0
    answer_tokens = frozenset(tokenize(answer))
    total = 0.0
    for fact in required_facts:
        fact_tokens = tuple(token for token in tokenize(fact) if token not in _STOPWORDS)
        if not fact_tokens:
            continue
        present = sum(1 for token in fact_tokens if token in answer_tokens)
        total += present / len(fact_tokens)
    return total / len(required_facts)


class AnswerEvaluationRunner:
    """Run the full RAG path and score answers deterministically and by optional judge."""

    def __init__(
        self,
        *,
        corpus: EvaluationCorpus,
        retrieval: RetrievalOrchestrator,
        generators: Mapping[str, AnswerGenerator],
        context_policies: Mapping[str, ContextPolicy],
        validator: CitationValidator | None = None,
        judge: AnswerJudge | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        if not generators:
            raise ValueError("at least one answer approach is required")
        missing_policies = set(generators) - set(context_policies)
        if missing_policies:
            raise ValueError(
                f"missing context policies for approaches: {', '.join(sorted(missing_policies))}"
            )
        self._corpus = corpus
        self._retrieval = retrieval
        self._generators = generators
        self._context_policies = context_policies
        self._validator = validator or CitationValidator()
        self._judge = judge
        self._telemetry = telemetry or NoOpTelemetry()

    def run(
        self,
        cases: tuple[SyntheticEvaluationCase, ...],
        *,
        strategy: RetrievalStrategyName,
        generation_id: UUID | None = None,
        approaches: Sequence[str] | None = None,
    ) -> tuple[tuple[AnswerEvaluationResult, ...], tuple[AnswerEvaluationSummary, ...]]:
        if not cases:
            raise ValueError("evaluation dataset must not be empty")
        selected_approaches = tuple(approaches or self._generators.keys())
        unknown = set(selected_approaches) - set(self._generators)
        if unknown:
            raise ValueError(f"unknown answer approaches: {', '.join(sorted(unknown))}")
        selected_generation = generation_id or self._corpus.active_generation_id()
        dataset_versions = {case.dataset_version for case in cases}
        if len(dataset_versions) != 1:
            raise ValueError("evaluation file mixes dataset versions")
        results: list[AnswerEvaluationResult] = []
        with self._telemetry.span(
            "evaluation.answer_run",
            {
                "evaluation.case_count": len(cases),
                "retrieval.version": strategy.value,
                "evaluation.approaches": ",".join(selected_approaches),
            },
        ):
            for case in cases:
                if not case.no_answer:
                    validate_answer_target(
                        self._corpus,
                        case,
                        selected_generation,
                    )
                results.extend(
                    self._evaluate_case(
                        case,
                        approach=approach,
                        strategy=strategy,
                        selected_generation=selected_generation,
                    )
                    for approach in selected_approaches
                )
        summaries = tuple(
            self._summarize(
                tuple(result for result in results if result.approach == approach),
                dataset_version=next(iter(dataset_versions)),
                approach=approach,
                strategy=strategy.value,
            )
            for approach in selected_approaches
        )
        return tuple(results), summaries

    def _evaluate_case(
        self,
        case: SyntheticEvaluationCase,
        *,
        approach: str,
        strategy: RetrievalStrategyName,
        selected_generation: UUID,
    ) -> AnswerEvaluationResult:
        started = perf_counter()
        retrieval = self._retrieval.retrieve(
            case.question,
            strategy=strategy,
            generation_id=selected_generation,
        )
        evidence = bound_evidence(retrieval.evidence, policy=self._context_policies[approach])
        generation_started = perf_counter()
        generated = self._generators[approach].generate(
            question=case.question,
            history=(),
            evidence=evidence,
        )
        generation_latency = perf_counter() - generation_started
        total_latency = perf_counter() - started

        citation_error: str | None = None
        try:
            resolved = self._validator.validate(generated, evidence)
            resolved_ids = tuple(item.citation_id for item in resolved)
        except AnswerValidationError as error:
            citation_error = str(error)
            resolved_ids = ()

        abstained_correctly: bool | None = None
        unexpected_abstention = False
        if case.no_answer:
            abstained_correctly = not generated.sufficient_evidence
        elif not generated.sufficient_evidence:
            unexpected_abstention = True

        judge_result: AnswerJudgeResult | None = None
        judge_error: str | None = None
        if self._judge is not None:
            try:
                judge_result = self._judge.judge(
                    question=case.question,
                    answer=generated.answer,
                    reference_answer=case.reference_answer,
                    required_facts=case.required_facts,
                    supporting_excerpt=case.supporting_excerpt,
                    no_answer=case.no_answer,
                )
            except RuntimeError as error:
                judge_error = str(error)

        return AnswerEvaluationResult(
            case_id=case.case_id,
            approach=approach,
            retrieval_strategy=strategy.value,
            question=case.question,
            question_type=case.question_type,
            difficulty=case.difficulty,
            no_answer=case.no_answer,
            answer=generated.answer,
            sufficient_evidence=generated.sufficient_evidence,
            abstained_correctly=abstained_correctly,
            unexpected_abstention=unexpected_abstention,
            declared_citations=tuple(dict.fromkeys(generated.citation_ids)),
            marker_citations=extract_citation_markers(generated.answer),
            resolved_citations=resolved_ids,
            citation_error=citation_error,
            citation_coverage=_citation_coverage(generated.answer),
            required_fact_lexical_coverage=_required_fact_lexical_coverage(
                generated.answer, case.required_facts
            ),
            generation_model=generated.model,
            generation_input_tokens=generated.input_tokens,
            generation_output_tokens=generated.output_tokens,
            generation_latency_seconds=generation_latency,
            total_latency_seconds=total_latency,
            judge=judge_result,
            judge_error=judge_error,
        )

    def _summarize(
        self,
        results: tuple[AnswerEvaluationResult, ...],
        *,
        dataset_version: str,
        approach: str,
        strategy: str,
    ) -> AnswerEvaluationSummary:
        total = len(results)
        answerable = tuple(result for result in results if not result.no_answer)
        no_answer = tuple(result for result in results if result.no_answer)
        judged: tuple[AnswerJudgeResult, ...] = tuple(
            result.judge for result in results if result.judge is not None
        )
        return AnswerEvaluationSummary(
            dataset_version=dataset_version,
            approach=approach,
            retrieval_strategy=strategy,
            cases=total,
            answerable_cases=len(answerable),
            no_answer_cases=len(no_answer),
            citation_validity_rate=(
                sum(1 for result in results if result.citation_error is None) / total
                if total
                else 0.0
            ),
            citation_coverage_mean=_mean(
                [result.citation_coverage for result in answerable]
            )
            or 0.0,
            required_fact_lexical_coverage_mean=_mean(
                [result.required_fact_lexical_coverage for result in answerable]
            )
            or 0.0,
            no_answer_abstention_rate=(
                sum(1 for result in no_answer if result.abstained_correctly)
                / len(no_answer)
                if no_answer
                else None
            ),
            unexpected_abstention_rate=(
                sum(1 for result in answerable if result.unexpected_abstention)
                / len(answerable)
                if answerable
                else 0.0
            ),
            mean_generation_latency_seconds=_mean(
                [result.generation_latency_seconds for result in results]
            )
            or 0.0,
            mean_total_latency_seconds=_mean(
                [result.total_latency_seconds for result in results]
            )
            or 0.0,
            mean_generation_input_tokens=_mean(
                [float(result.generation_input_tokens or 0) for result in results]
            )
            or 0.0,
            mean_generation_output_tokens=_mean(
                [float(result.generation_output_tokens or 0) for result in results]
            )
            or 0.0,
            judge_applied=bool(judged),
            judge_rubric_version=judged[0].rubric_version if judged else None,
            judge_model=judged[0].model if judged else None,
            mean_factual_correctness=_mean(
                [float(judge.factual_correctness) for judge in judged]
            ),
            mean_groundedness=_mean([float(judge.groundedness) for judge in judged]),
            mean_completeness=_mean([float(judge.completeness) for judge in judged]),
            mean_relevance_concision=_mean(
                [float(judge.relevance_concision) for judge in judged]
            ),
            mean_uncertainty=_mean([float(judge.uncertainty) for judge in judged]),
            mean_overall=_mean([float(judge.overall) for judge in judged]),
            by_question_type=_slice_breakdowns(results, key=lambda result: result.question_type),
            by_difficulty=_slice_breakdowns(results, key=lambda result: result.difficulty),
        )


def _slice_breakdowns(
    results: tuple[AnswerEvaluationResult, ...],
    *,
    key: Callable[[AnswerEvaluationResult], str],
) -> tuple[AnswerSliceBreakdown, ...]:
    """Aggregate results into ordered public-safe slice breakdowns."""

    groups: dict[str, list[AnswerEvaluationResult]] = {}
    for result in results:
        groups.setdefault(key(result) or "unknown", []).append(result)
    breakdowns: list[AnswerSliceBreakdown] = []
    for label in sorted(groups):
        items = tuple(groups[label])
        no_answer_items = tuple(item for item in items if item.no_answer)
        judged: tuple[AnswerJudgeResult, ...] = tuple(
            item.judge for item in items if item.judge is not None
        )
        breakdowns.append(
            AnswerSliceBreakdown(
                label=label,
                cases=len(items),
                citation_validity_rate=(
                    sum(1 for item in items if item.citation_error is None) / len(items)
                ),
                citation_coverage_mean=_mean(
                    [item.citation_coverage for item in items if not item.no_answer]
                )
                or 0.0,
                no_answer_abstention_rate=(
                    sum(1 for item in no_answer_items if item.abstained_correctly)
                    / len(no_answer_items)
                    if no_answer_items
                    else None
                ),
                mean_factual_correctness=_mean(
                    [float(judge.factual_correctness) for judge in judged]
                ),
                mean_groundedness=_mean([float(judge.groundedness) for judge in judged]),
                mean_completeness=_mean([float(judge.completeness) for judge in judged]),
                mean_relevance_concision=_mean(
                    [float(judge.relevance_concision) for judge in judged]
                ),
                mean_uncertainty=_mean([float(judge.uncertainty) for judge in judged]),
                mean_overall=_mean([float(judge.overall) for judge in judged]),
            )
        )
    return tuple(breakdowns)


def render_answer_evaluation_markdown(
    summaries: tuple[AnswerEvaluationSummary, ...],
) -> str:
    """Render a public-safe Markdown summary without questions, answers, or content."""

    lines = [
        "# Answer Evaluation Summary",
        "",
        "This summary is public-safe: it contains aggregates only and never questions, "
        "answers, prompts, or source content.",
        "",
        "## Approach comparison",
        "",
        "| Approach | Cases | Citation validity | Citation coverage | No-answer abstention | "
        "Fact lexical coverage | Judge overall |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for summary in sorted(summaries, key=lambda item: item.approach):
        lines.extend(
            [
                f"| {summary.approach} | {summary.cases} | "
                f"{summary.citation_validity_rate:.2%} | "
                f"{summary.citation_coverage_mean:.2f} | "
                f"{_format_optional_rate(summary.no_answer_abstention_rate)} | "
                f"{summary.required_fact_lexical_coverage_mean:.2f} | "
                f"{_format_optional_score(summary.mean_overall)} |",
            ]
        )
    for summary in sorted(summaries, key=lambda item: item.approach):
        lines.extend(
            [
                "",
                f"## {summary.approach}",
                "",
                f"- Dataset version: `{summary.dataset_version}`",
                f"- Retrieval strategy: `{summary.retrieval_strategy}`",
                f"- Cases: {summary.cases} (answerable {summary.answerable_cases}, "
                f"no-answer {summary.no_answer_cases})",
                f"- Citation validity: {summary.citation_validity_rate:.2%}",
                f"- Citation coverage (mean): {summary.citation_coverage_mean:.2f}",
                f"- Required-fact lexical coverage (mean): "
                f"{summary.required_fact_lexical_coverage_mean:.2f}",
                f"- No-answer abstention rate: "
                f"{_format_optional_rate(summary.no_answer_abstention_rate)}",
                f"- Unexpected abstention rate: {summary.unexpected_abstention_rate:.2%}",
                f"- Mean latency: {summary.mean_total_latency_seconds:.3f}s "
                f"(generation {summary.mean_generation_latency_seconds:.3f}s)",
                f"- Mean tokens: {summary.mean_generation_input_tokens:.0f} in / "
                f"{summary.mean_generation_output_tokens:.0f} out",
            ]
        )
        if summary.judge_applied:
            lines.extend(
                [
                    "",
                    f"- Judge model: `{summary.judge_model}`; "
                    f"rubric `{summary.judge_rubric_version}`",
                    f"- Mean factual correctness: "
                    f"{_format_optional_score(summary.mean_factual_correctness)}",
                    f"- Mean groundedness: "
                    f"{_format_optional_score(summary.mean_groundedness)}",
                    f"- Mean completeness: "
                    f"{_format_optional_score(summary.mean_completeness)}",
                    f"- Mean relevance/concision: "
                    f"{_format_optional_score(summary.mean_relevance_concision)}",
                    f"- Mean uncertainty: "
                    f"{_format_optional_score(summary.mean_uncertainty)}",
                    f"- Mean overall: {_format_optional_score(summary.mean_overall)}",
                    "",
                    "> Judge scores are uncalibrated model opinions, not ground truth. "
                    "Calibrate them against human labels before treating them as "
                    "authoritative.",
                ]
            )
        lines.extend(["", "### By question type", ""])
        lines.extend(_slice_table(summary.by_question_type))
        lines.extend(["", "### By difficulty", ""])
        lines.extend(_slice_table(summary.by_difficulty))
    return "\n".join(lines) + "\n"


def _format_optional_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _format_optional_score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _slice_table(breakdowns: tuple[AnswerSliceBreakdown, ...]) -> list[str]:
    rows = [
        "| Slice | Cases | Citation validity | Citation coverage | Judge overall |",
        "| --- | --- | --- | --- | --- |",
    ]
    for breakdown in breakdowns:
        rows.extend(
            [
                f"| {breakdown.label} | {breakdown.cases} | "
                f"{breakdown.citation_validity_rate:.2%} | "
                f"{breakdown.citation_coverage_mean:.2f} | "
                f"{_format_optional_score(breakdown.mean_overall)} |",
            ]
        )
    return rows


def _retrieval_slices(
    results: tuple[RetrievalEvaluationResult, ...],
    *,
    key: Callable[[RetrievalEvaluationResult], str],
) -> tuple[RetrievalSliceBreakdown, ...]:
    """Aggregate retrieval results into ordered public-safe slice breakdowns."""

    groups: dict[str, list[RetrievalEvaluationResult]] = {}
    for result in results:
        groups.setdefault(key(result) or "unknown", []).append(result)
    breakdowns: list[RetrievalSliceBreakdown] = []
    for label in sorted(groups):
        items = tuple(groups[label])
        answerable = tuple(item for item in items if not item.no_answer)
        no_answer_items = tuple(item for item in items if item.no_answer)
        answerable_total = len(answerable)
        breakdowns.append(
            RetrievalSliceBreakdown(
                label=label,
                cases=len(items),
                answerable_cases=answerable_total,
                hit_at_5=(
                    sum(item.hit_at_5 for item in answerable) / answerable_total
                    if answerable_total
                    else 0.0
                ),
                hit_at_20=(
                    sum(item.hit_at_20 for item in answerable) / answerable_total
                    if answerable_total
                    else 0.0
                ),
                mean_reciprocal_rank=(
                    sum(item.reciprocal_rank for item in answerable) / answerable_total
                    if answerable_total
                    else 0.0
                ),
                mean_latency_seconds=(
                    sum(item.latency_seconds for item in items) / len(items)
                ),
                no_answer_cases=len(no_answer_items),
                no_answer_false_positive_rate=(
                    sum(1 for item in no_answer_items if item.false_positive)
                    / len(no_answer_items)
                    if no_answer_items
                    else None
                ),
            )
        )
    return tuple(breakdowns)


def load_sample_manifest(path: Path | str) -> SampleManifest:
    """Load and validate a public-safe sample corpus manifest."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("sample manifest must be a JSON object")

    def required_string(key: str) -> str:
        item = payload.get(key)
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"sample manifest requires non-empty {key}")
        return item

    def string_list(item: object, key: str) -> tuple[str, ...]:
        if not isinstance(item, list) or any(
            not isinstance(entry, str) or not entry.strip() for entry in item
        ):
            raise ValueError(f"sample manifest requires {key} to be a list of strings")
        return tuple(item)

    sources_value = payload.get("sources")
    cases_value = payload.get("cases")
    if not isinstance(sources_value, list) or not isinstance(cases_value, list):
        raise ValueError("sample manifest requires sources and cases lists")
    sources: list[SampleSource] = []
    for entry in sources_value:
        if not isinstance(entry, dict):
            raise ValueError("sample manifest source must be an object")
        sources.append(
            SampleSource(
                source_id=required_entry(entry, "source_id"),
                title=required_entry(entry, "title"),
                url=required_entry(entry, "url"),
                provider=required_entry(entry, "provider"),
                provenance=required_entry(entry, "provenance"),
            )
        )
    cases: list[SampleCase] = []
    for entry in cases_value:
        if not isinstance(entry, dict):
            raise ValueError("sample manifest case must be an object")
        source_id = optional_entry(entry, "source_id")
        distractor = optional_entry(entry, "distractor_source_id")
        cases.append(
            SampleCase(
                case_id=required_entry(entry, "case_id"),
                source_id=source_id,
                question=required_entry(entry, "question"),
                reference_answer=required_entry(entry, "reference_answer"),
                required_facts=string_list(entry.get("required_facts"), "required_facts"),
                question_type=required_entry(entry, "question_type"),
                difficulty=required_entry(entry, "difficulty"),
                no_answer=bool(entry.get("no_answer", False)),
                distractor_source_id=distractor,
            )
        )
    return SampleManifest(
        manifest_version=required_string("manifest_version"),
        dataset_version=required_string("dataset_version"),
        description=required_string("description"),
        sources=tuple(sources),
        cases=tuple(cases),
    )


def required_entry(entry: dict[str, object], key: str) -> str:
    item = entry.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"sample manifest entry requires non-empty {key}")
    return item


def optional_entry(entry: dict[str, object], key: str) -> str | None:
    item = entry.get(key)
    if not isinstance(item, str) or not item.strip():
        return None
    return item


def sample_cases_to_dataset(manifest: SampleManifest) -> tuple[SyntheticEvaluationCase, ...]:
    """Convert public-safe manifest cases into document-level evaluation cases."""

    sources_by_id = {source.source_id: source for source in manifest.sources}
    cases: list[SyntheticEvaluationCase] = []
    for case in manifest.cases:
        source = sources_by_id.get(case.source_id or "")
        if case.no_answer:
            distractor = (
                sources_by_id.get(case.distractor_source_id or "")
                if case.distractor_source_id
                else None
            )
            cases.append(
                SyntheticEvaluationCase(
                    case_id=case.case_id,
                    dataset_version=manifest.dataset_version,
                    target_chunk_id=None,
                    target_document_id=distractor.source_id if distractor else None,
                    target_revision_id=None,
                    content_fingerprint=None,
                    question=case.question,
                    reference_answer=case.reference_answer,
                    required_facts=(),
                    supporting_excerpt="",
                    acceptable_chunk_ids=(),
                    source_provider=distractor.provider if distractor else "sample",
                    question_type=case.question_type,
                    difficulty=case.difficulty,
                    generator_model="not-recorded",
                    generator_prompt_version="sample-curated-v1",
                    no_answer=True,
                    distractor_chunk_ids=(),
                    document_level=True,
                    target_url=distractor.url if distractor else None,
                )
            )
            continue
        if source is None:
            raise ValueError(
                f"sample case {case.case_id} references unknown source {case.source_id}"
            )
        cases.append(
            SyntheticEvaluationCase(
                case_id=case.case_id,
                dataset_version=manifest.dataset_version,
                target_chunk_id=None,
                target_document_id=source.source_id,
                target_revision_id=None,
                content_fingerprint=None,
                question=case.question,
                reference_answer=case.reference_answer,
                required_facts=case.required_facts,
                supporting_excerpt="",
                acceptable_chunk_ids=(),
                source_provider=source.provider,
                question_type=case.question_type,
                difficulty=case.difficulty,
                generator_model="not-recorded",
                generator_prompt_version="sample-curated-v1",
                document_level=True,
                target_url=source.url,
            )
        )
    return tuple(cases)


_JUDGE_DIMENSIONS = (
    "factual_correctness",
    "groundedness",
    "completeness",
    "relevance_concision",
    "uncertainty",
    "overall",
)


@dataclass(frozen=True, slots=True)
class JudgeCalibrationDimension:
    """Agreement between model-judge scores and reviewed human labels on one axis."""

    name: str
    mean_absolute_error: float | None
    bias: float | None
    pearson: float | None


@dataclass(frozen=True, slots=True)
class JudgeCalibrationReport:
    """How far the LLM judge drifts from a human-scored subset.

    A report with ``human_label_count == 0`` means calibration is ``not_run``:
    judge scores are uncalibrated model opinions until a human reviews a subset.
    """

    human_label_count: int
    matched_result_count: int
    judge_model: str | None
    judge_prompt_version: str | None = None
    judge_rubric_version: str | None = None
    dimensions: tuple[JudgeCalibrationDimension, ...] = ()


def load_human_labels(path: Path) -> tuple[dict[str, object], ...]:
    """Load reviewed human labels and validate the fixed scoring schema.

    Each JSONL row must carry ``case_id``, ``approach``, and the six 0-5 judge
    dimension scores. Human labels are ground truth for calibration, so invalid
    rows fail closed instead of being silently ignored.
    """

    labels: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"human label line {line_number} is not an object")
        missing = [
            key
            for key in ("case_id", "approach", *_JUDGE_DIMENSIONS)
            if key not in value
        ]
        if missing:
            raise ValueError(
                f"human label line {line_number} is missing fields: {', '.join(missing)}"
            )
        for dimension in _JUDGE_DIMENSIONS:
            score = value[dimension]
            if not isinstance(score, int) or not 0 <= score <= 5:
                raise ValueError(
                    f"human label line {line_number} {dimension} must be an int in 0..5"
                )
        labels.append(value)
    return tuple(labels)


def calibrate_judge_scores(
    human_labels: Sequence[Mapping[str, object]],
    judge_results: Sequence[tuple[str, str, Mapping[str, object]]],
) -> JudgeCalibrationReport:
    """Compare model-judge scores to reviewed human labels per (case_id, approach).

    ``judge_results`` items are ``(case_id, approach, metadata+scores)`` triples
    taken from real ``answer-eval-run`` output: each scores mapping carries the
    six 0-5 dimension scores plus the judge ``model``, ``prompt_version``, and
    ``rubric_version`` metadata. Only matched pairs are compared. A missing or
    empty human subset yields an empty report: calibration is ``not_run``, never
    approximated.
    """

    by_key = {(case_id, approach): scores for case_id, approach, scores in judge_results}
    matched = [
        (label, by_key[(str(label["case_id"]), str(label["approach"]))])
        for label in human_labels
        if (str(label["case_id"]), str(label["approach"])) in by_key
    ]
    judge_model = next(
        (str(result[2]["model"]) for result in judge_results if result[2].get("model")),
        None,
    )
    judge_prompt_version = next(
        (
            str(result[2]["prompt_version"])
            for result in judge_results
            if result[2].get("prompt_version")
        ),
        None,
    )
    judge_rubric_version = next(
        (
            str(result[2]["rubric_version"])
            for result in judge_results
            if result[2].get("rubric_version")
        ),
        None,
    )
    return JudgeCalibrationReport(
        human_label_count=len(human_labels),
        matched_result_count=len(matched),
        judge_model=judge_model,
        judge_prompt_version=judge_prompt_version,
        judge_rubric_version=judge_rubric_version,
        dimensions=tuple(
            _calibrate_dimension(name, matched) for name in _JUDGE_DIMENSIONS
        ),
    )


def _calibrate_dimension(
    name: str,
    matched: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
) -> JudgeCalibrationDimension:
    human = [float(str(label[name])) for label, _ in matched]
    judge = [float(str(scores[name])) for _, scores in matched]
    if not human:
        return JudgeCalibrationDimension(name, None, None, None)
    errors = [j - h for h, j in zip(human, judge, strict=False)]
    mean_absolute_error = sum(abs(error) for error in errors) / len(errors)
    bias = sum(errors) / len(errors)
    pearson: float | None = None
    if len(errors) >= 2:
        try:
            pearson = statistics.correlation(human, judge)
        except statistics.StatisticsError:
            pearson = None
    return JudgeCalibrationDimension(name, mean_absolute_error, bias, pearson)


def render_calibration_markdown(report: JudgeCalibrationReport) -> str:
    """Render a public-safe calibration summary (aggregates only)."""

    lines = [
        "# Judge Calibration Report",
        "",
        "Model-judge scores compared against reviewed human labels on the matched "
        "(case, approach) subset. This report only exists once human labels have "
        "been reviewed; until then calibration is `not_run` and judge scores are "
        "uncalibrated model opinions.",
        "",
        f"- Human labels reviewed: {report.human_label_count}",
        f"- Judge results matched: {report.matched_result_count}",
        f"- Judge model: {report.judge_model or 'n/a'}",
        f"- Judge prompt: {report.judge_prompt_version or 'n/a'}; "
        f"rubric: {report.judge_rubric_version or 'n/a'}",
        "",
        "| Dimension | Mean absolute error | Bias (judge - human) | Pearson r |",
        "| --- | --- | --- | --- |",
    ]
    for dimension in report.dimensions:
        lines.extend(
            [
                f"| {dimension.name} | {_fmt_optional(dimension.mean_absolute_error)} | "
                f"{_fmt_optional(dimension.bias)} | {_fmt_optional(dimension.pearson)} |",
            ]
        )
    lines.append("")
    lines.append(
        "Interpretation: MAE is the average per-case distance between judge and "
        "human scores; bias is the average signed drift (positive means the judge "
        "scores higher than the human); Pearson r measures monotonic agreement. "
        "Deterministic metrics and judge scores are never presented as human truth."
    )
    return "\n".join(lines) + "\n"


def _fmt_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"
