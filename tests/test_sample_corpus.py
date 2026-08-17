"""Sample corpus manifest, document-level retrieval evaluation, and planner metrics."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from knowledge_assistant.application.evaluation import (
    RetrievalEvaluationRunner,
    load_dataset,
    load_sample_manifest,
    sample_cases_to_dataset,
    write_jsonl,
)
from knowledge_assistant.domain.evaluation import (
    EvaluationChunk,
    SampleManifest,
    SyntheticEvaluationCase,
)
from knowledge_assistant.domain.query import Evidence
from knowledge_assistant.domain.retrieval import (
    QueryRoute,
    RetrievalResult,
    RetrievalStrategyName,
    RetrievalTrace,
)

GENERATION_ID = uuid.uuid4()

MANIFEST_PATH = Path("data/sample/manifest.json")


class FakeCorpus:
    def __init__(
        self,
        document_chunk_ids: tuple[str, ...] = ("chunk-a", "chunk-b"),
        document_id: str | None = "doc-sample",
    ) -> None:
        self._document_chunk_ids = document_chunk_ids
        self._document_id = document_id

    def active_generation_id(self) -> uuid.UUID:
        return GENERATION_ID

    def sample_chunks(self, **_kwargs: object) -> tuple[EvaluationChunk, ...]:
        return ()

    def validate_chunk(self, **_kwargs: object) -> bool:
        return True

    def document_chunks(self, **_kwargs: object) -> tuple[str, ...]:
        return self._document_chunk_ids

    def document_id_for_url(self, **_kwargs: object) -> str | None:
        return self._document_id


class FakeRetrieval:
    def __init__(
        self,
        ordered_ids: tuple[str, ...] = ("other", "chunk-a"),
        *,
        planner: bool = False,
    ) -> None:
        self._ordered_ids = ordered_ids
        self._planner = planner

    def retrieve(self, question: str, **kwargs: object) -> RetrievalResult:
        assert question
        strategy = cast(RetrievalStrategyName, kwargs["strategy"])
        evidence = tuple(
            Evidence(
                citation_id=f"E{index}",
                chunk_id=chunk_id,
                document_id="doc",
                revision_id="rev",
                title="T",
                source_url="https://example.com",
                vault_path="p.md",
                heading_path=(),
                content="content",
                score=1.0 - index / 10,
            )
            for index, chunk_id in enumerate(self._ordered_ids, start=1)
        )
        trace = RetrievalTrace(
            strategy=strategy,
            route=QueryRoute.COMPLEX if self._planner else QueryRoute.SIMPLE,
            subqueries=(question, "extra"),
            retrieval_rounds=2 if self._planner else 1,
            stop_reason="complete",
            planner_model="planner-model" if self._planner else None,
            planner_input_tokens=11 if self._planner else None,
            planner_output_tokens=7 if self._planner else None,
        )
        return RetrievalResult(evidence=evidence, trace=trace)


def load_manifest() -> SampleManifest:
    return load_sample_manifest(MANIFEST_PATH)


def dataset() -> tuple[SyntheticEvaluationCase, ...]:
    return sample_cases_to_dataset(load_manifest())


def test_committed_manifest_is_public_safe() -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert payload["manifest_version"] == "sample-corpus-v1"
    assert len(payload["sources"]) >= 4
    assert len(payload["cases"]) >= 20
    for source in payload["sources"]:
        assert set(source) == {"source_id", "title", "url", "provider", "provenance"}
        assert source["url"].startswith("https://")
    for case in payload["cases"]:
        assert case["question"]
        assert case["reference_answer"]
        assert case["question_type"] in {
            "fact",
            "explanation",
            "comparison",
            "exact_lookup",
            "insufficient_evidence",
            "synthesis",
            "follow_up",
            "hard_negative",
        }
        assert case["difficulty"] in {"easy", "medium", "hard"}


def test_committed_manifest_has_required_question_mix() -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    types = {case["question_type"] for case in payload["cases"]}
    no_answer = [case for case in payload["cases"] if case.get("no_answer")]
    assert len(no_answer) >= 3  # at least three insufficient-evidence cases
    # Required mix: single-doc, explanation, exact lookup, comparison,
    # multi-document synthesis, follow-up style, hard negatives.
    assert {
        "explanation",
        "exact_lookup",
        "comparison",
        "synthesis",
        "follow_up",
        "hard_negative",
        "insufficient_evidence",
    } <= types
    assert any(case["question_type"] == "fact" for case in payload["cases"])
    # No article bodies or long excerpts: questions and answers are concise
    # original wording (length sanity bound), and no case stores article text.
    for case in payload["cases"]:
        assert len(case["question"]) <= 400
        assert len(case["reference_answer"]) <= 900
        assert all(len(fact) <= 220 for fact in case["required_facts"])


def test_manifest_loader_rejects_malformed_entries(tmp_path: Path) -> None:
    manifest = load_sample_manifest(MANIFEST_PATH)
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    raw["cases"][0]["case_id"] = ""
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="case_id"):
        load_sample_manifest(broken)
    assert manifest.dataset_version == "sample-docs-v1"


def test_sample_cases_to_dataset_builds_document_level_cases() -> None:
    cases = dataset()
    answerable = [case for case in cases if not case.no_answer]
    no_answer = [case for case in cases if case.no_answer]

    assert len(cases) == len(load_manifest().cases)
    assert all(case.document_level for case in answerable)
    assert all(case.target_document_id is not None for case in answerable)
    assert all(case.target_chunk_id is None for case in answerable)
    assert all(case.generator_model == "not-recorded" for case in answerable)
    assert all(case.generator_prompt_version == "sample-curated-v1" for case in answerable)
    assert all(case.dataset_version == "sample-docs-v1" for case in cases)

    assert len(no_answer) == 3
    assert all(case.no_answer for case in no_answer)
    assert all(case.question_type == "insufficient_evidence" for case in no_answer)
    assert all(case.required_facts == () for case in no_answer)
    assert {case.target_document_id for case in no_answer} == {
        "sample-21-lessons",
        "sample-software-factories",
    }


def test_sample_dataset_jsonl_round_trip(tmp_path: Path) -> None:
    cases = dataset()
    output = tmp_path / "sample.jsonl"
    write_jsonl(output, tuple(case.as_dict() for case in cases))

    loaded = load_dataset(output)

    assert loaded == cases
    assert loaded[0].document_level is True


def test_runner_resolves_document_level_targets() -> None:
    case = next(c for c in dataset() if not c.no_answer)
    runner = RetrievalEvaluationRunner(
        corpus=FakeCorpus(document_chunk_ids=("chunk-a", "chunk-b")),
        retrieval=cast(Any, FakeRetrieval(ordered_ids=("other", "chunk-a"))),
    )

    results, summary = runner.run(
        (case,),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
    )

    assert results[0].target_rank == 2
    assert results[0].hit_at_5 is True
    assert results[0].false_positive is None
    assert summary.hit_at_5 == 1.0
    assert summary.cases == 1


def test_runner_no_answer_false_positive_uses_distractor_document() -> None:
    no_answer = next(c for c in dataset() if c.no_answer)
    runner = RetrievalEvaluationRunner(
        corpus=FakeCorpus(document_chunk_ids=("chunk-a", "chunk-b")),
        retrieval=cast(Any, FakeRetrieval(ordered_ids=("other", "chunk-a"))),
    )

    results, summary = runner.run(
        (no_answer,),
        strategy=RetrievalStrategyName.WEIGHTED_HYBRID,
    )

    assert results[0].no_answer is True
    assert results[0].false_positive is True
    assert summary.no_answer_cases == 1
    assert summary.no_answer_false_positive_rate == 1.0
    assert summary.hit_at_5 == 0.0


def test_runner_records_planner_calls_and_tokens() -> None:
    case = next(c for c in dataset() if not c.no_answer)
    runner = RetrievalEvaluationRunner(
        corpus=FakeCorpus(document_chunk_ids=("chunk-a",)),
        retrieval=cast(Any, FakeRetrieval(ordered_ids=("chunk-a",), planner=True)),
    )

    results, summary = runner.run(
        (case,),
        strategy=RetrievalStrategyName.AGENTIC_DECOMPOSITION,
    )

    assert results[0].planner_calls == 1
    assert results[0].planner_input_tokens == 11
    assert results[0].planner_output_tokens == 7
    assert results[0].route == "complex"
    assert summary.mean_planner_calls == 1.0
    assert summary.mean_planner_input_tokens == 11.0
    assert summary.mean_planner_output_tokens == 7.0


def test_runner_records_type_and_difficulty_breakdowns() -> None:
    cases = [case for case in dataset() if not case.no_answer][:3]
    runner = RetrievalEvaluationRunner(
        corpus=FakeCorpus(document_chunk_ids=("chunk-a",)),
        retrieval=cast(Any, FakeRetrieval(ordered_ids=("chunk-a",))),
    )

    _results, summary = runner.run(
        tuple(cases),
        strategy=RetrievalStrategyName.VECTOR_ONLY,
    )

    by_type = {breakdown.label: breakdown for breakdown in summary.by_question_type}
    assert "fact" in by_type
    assert "explanation" in by_type
    assert by_type["fact"].answerable_cases == 1
    assert by_type["fact"].hit_at_5 == 1.0
    by_difficulty = {breakdown.label: breakdown for breakdown in summary.by_difficulty}
    assert "easy" in by_difficulty
    assert by_difficulty["easy"].no_answer_cases == 0


def test_runner_requires_target_document_for_document_level() -> None:
    broken = SyntheticEvaluationCase(
        case_id="broken",
        dataset_version="sample-docs-v1",
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
        generator_prompt_version="sample-human-v1",
        document_level=True,
    )
    runner = RetrievalEvaluationRunner(
        corpus=FakeCorpus(),
        retrieval=cast(Any, FakeRetrieval()),
    )

    with pytest.raises(ValueError, match="requires a target document URL"):
        runner.run((broken,), strategy=RetrievalStrategyName.VECTOR_ONLY)


def test_runner_reports_uningested_target_document() -> None:
    case = next(c for c in dataset() if not c.no_answer)
    runner = RetrievalEvaluationRunner(
        corpus=FakeCorpus(document_id=None),
        retrieval=cast(Any, FakeRetrieval()),
    )

    with pytest.raises(RuntimeError, match="target document is not ingested"):
        runner.run((case,), strategy=RetrievalStrategyName.VECTOR_ONLY)
