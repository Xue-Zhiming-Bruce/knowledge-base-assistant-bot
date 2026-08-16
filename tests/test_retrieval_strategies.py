from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from knowledge_assistant.application.retrieval import RetrievalOrchestrator
from knowledge_assistant.domain.query import Evidence
from knowledge_assistant.domain.retrieval import (
    DiversityReranker,
    QueryPlan,
    QueryRoute,
    ReciprocalRankFusion,
    RetrievalStrategyName,
)
from knowledge_assistant.infrastructure.openai.planning import OpenAIQueryPlanner
from knowledge_assistant.ports.embeddings import EmbeddingBatch


def evidence(chunk_id: str, score: float, *, document_id: str | None = None) -> Evidence:
    return Evidence(
        citation_id="unused",
        chunk_id=chunk_id,
        document_id=document_id or f"doc-{chunk_id}",
        revision_id=f"rev-{chunk_id}",
        title=f"Title {chunk_id}",
        source_url="https://example.com/source",
        vault_path="Articles/source.md",
        heading_path=(),
        content=f"Evidence {chunk_id}",
        score=score,
    )


def test_reciprocal_rank_fusion_uses_rank_and_deterministic_ties() -> None:
    vector = (evidence("a", 0.99), evidence("b", 0.8))
    lexical = (evidence("b", 4.0), evidence("c", 3.0))

    fused = ReciprocalRankFusion(rank_constant=60).fuse(
        (vector, lexical),
        limit=3,
    )

    assert [item.chunk_id for item in fused] == ["b", "a", "c"]
    assert [item.citation_id for item in fused] == ["E1", "E2", "E3"]
    assert fused[0].score > fused[1].score
    with pytest.raises(ValueError, match="positive"):
        ReciprocalRankFusion(rank_constant=0)


class FakeEmbeddings:
    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        vectors = tuple((float(index), 0.5) for index, _text in enumerate(texts, 1))
        return EmbeddingBatch(vectors, "embedding-test", 2, len(texts))


class FakeRetriever:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def retrieve(self, **kwargs: object) -> tuple[Evidence, ...]:
        self.calls.append(kwargs)
        query = cast(str, kwargs["query_text"])
        if query == "first fact":
            return (evidence("a", 0.9), evidence("shared", 0.8))
        if query == "second fact":
            return (evidence("shared", 0.95), evidence("b", 0.8))
        return (evidence("single", 0.9),)


class FakePlanner:
    VERSION = "planner-test"

    def plan(self, _question: str) -> QueryPlan:
        return QueryPlan(
            route=QueryRoute.COMPLEX,
            subqueries=("first fact", "second fact"),
            reason="Needs both facts.",
            model="planner-test",
        )


def test_retrieval_orchestrator_runs_single_and_bounded_agentic_routes() -> None:
    repository = FakeRetriever()
    orchestrator = RetrievalOrchestrator(
        repository=repository,
        embeddings=FakeEmbeddings(),
        reranker=DiversityReranker(),
        planner=FakePlanner(),
    )

    single = orchestrator.retrieve(
        "one fact",
        strategy=RetrievalStrategyName.VECTOR_ONLY,
    )
    agentic = orchestrator.retrieve(
        "compare two facts",
        strategy=RetrievalStrategyName.AGENTIC_DECOMPOSITION,
    )

    assert single.trace.route is QueryRoute.SIMPLE
    assert repository.calls[0]["strategy"] is RetrievalStrategyName.VECTOR_ONLY
    assert agentic.trace.route is QueryRoute.COMPLEX
    assert agentic.trace.retrieval_rounds == 2
    assert agentic.trace.subqueries == ("first fact", "second fact")
    assert [item.chunk_id for item in agentic.evidence][:1] == ["shared"]
    assert all(
        call["strategy"] is RetrievalStrategyName.RRF_HYBRID for call in repository.calls[1:]
    )


class FakePlannerResponses:
    def parse(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            output_parsed=SimpleNamespace(
                route=QueryRoute.COMPLEX,
                subqueries=["first", "second", "second", "third", "fourth"],
                reason="Multiple facts.",
            ),
            usage=SimpleNamespace(input_tokens=12, output_tokens=8),
            model="planner-model",
        )


def test_openai_planner_deduplicates_and_bounds_structured_subqueries() -> None:
    planner = OpenAIQueryPlanner(
        api_key="unused",
        model="planner-model",
        client=cast(Any, SimpleNamespace(responses=FakePlannerResponses())),
    )

    plan = planner.plan("Compare them")

    assert plan.route is QueryRoute.COMPLEX
    assert plan.subqueries == ("first", "second", "third")
    assert plan.input_tokens == 12
