"""Versioned retrieval orchestration shared by Question Mode and evaluation."""

from __future__ import annotations

from time import perf_counter
from uuid import UUID

from knowledge_assistant.domain.retrieval import (
    QueryRoute,
    ReciprocalRankFusion,
    RetrievalResult,
    RetrievalStrategyName,
    RetrievalTrace,
    relabel_evidence,
)
from knowledge_assistant.ports.embeddings import EmbeddingProvider
from knowledge_assistant.ports.retrieval import EvidenceRetriever, QueryPlanner, Reranker
from knowledge_assistant.ports.telemetry import NoOpTelemetry, Telemetry


class RetrievalOrchestrator:
    """Run single-pass or bounded agentic retrieval under one contract."""

    def __init__(
        self,
        *,
        repository: EvidenceRetriever,
        embeddings: EmbeddingProvider,
        reranker: Reranker,
        planner: QueryPlanner | None = None,
        telemetry: Telemetry | None = None,
        candidate_limit: int = 20,
        evidence_limit: int = 8,
        rrf_rank_constant: int = 60,
    ) -> None:
        if candidate_limit < evidence_limit or evidence_limit < 1:
            raise ValueError("retrieval limits must be positive and ordered")
        self._repository = repository
        self._embeddings = embeddings
        self._reranker = reranker
        self._planner = planner
        self._telemetry = telemetry or NoOpTelemetry()
        self._candidate_limit = candidate_limit
        self._evidence_limit = evidence_limit
        self._fusion = ReciprocalRankFusion(rank_constant=rrf_rank_constant)

    def retrieve(
        self,
        question: str,
        *,
        strategy: RetrievalStrategyName,
        generation_id: UUID | None = None,
    ) -> RetrievalResult:
        started = perf_counter()
        with self._telemetry.span(
            "retrieval",
            {"retrieval.version": strategy.value},
        ):
            if strategy is RetrievalStrategyName.AGENTIC_DECOMPOSITION:
                result = self._retrieve_agentic(question, generation_id=generation_id)
            else:
                result = self._retrieve_once(
                    question,
                    strategy=strategy,
                    generation_id=generation_id,
                )
        self._telemetry.observe(
            "question_stage_duration_seconds",
            perf_counter() - started,
            {"stage": "retrieval", "outcome": "success"},
        )
        self._telemetry.observe(
            "retrieval_candidates",
            float(len(result.evidence)),
            {"retrieval_version": strategy.value},
        )
        return result

    def _retrieve_once(
        self,
        question: str,
        *,
        strategy: RetrievalStrategyName,
        generation_id: UUID | None,
    ) -> RetrievalResult:
        batch = self._embeddings.embed((question,))
        evidence = self._repository.retrieve(
            query_text=question,
            query_vector=batch.vectors[0],
            embedding_model=batch.model,
            dimensions=batch.dimensions,
            strategy=strategy,
            limit=self._candidate_limit,
            generation_id=generation_id,
        )
        ranked = relabel_evidence(self._reranker.rank(evidence, limit=self._evidence_limit))
        return RetrievalResult(
            evidence=ranked,
            trace=RetrievalTrace(
                strategy=strategy,
                route=QueryRoute.SIMPLE,
                subqueries=(question,),
                retrieval_rounds=1,
                stop_reason="single_pass_complete",
            ),
        )

    def _retrieve_agentic(
        self,
        question: str,
        *,
        generation_id: UUID | None,
    ) -> RetrievalResult:
        if self._planner is None:
            raise RuntimeError("agentic retrieval requires a query planner")
        with self._telemetry.span("retrieval.plan"):
            plan = self._planner.plan(question)
        queries = plan.subqueries if plan.route is QueryRoute.COMPLEX else (question,)
        batch = self._embeddings.embed(queries)
        rankings = tuple(
            self._repository.retrieve(
                query_text=query,
                query_vector=vector,
                embedding_model=batch.model,
                dimensions=batch.dimensions,
                strategy=RetrievalStrategyName.RRF_HYBRID,
                limit=self._candidate_limit,
                generation_id=generation_id,
            )
            for query, vector in zip(queries, batch.vectors, strict=True)
        )
        merged = self._fusion.fuse(rankings, limit=self._candidate_limit)
        ranked = relabel_evidence(self._reranker.rank(merged, limit=self._evidence_limit))
        return RetrievalResult(
            evidence=ranked,
            trace=RetrievalTrace(
                strategy=RetrievalStrategyName.AGENTIC_DECOMPOSITION,
                route=plan.route,
                subqueries=queries,
                retrieval_rounds=len(queries),
                stop_reason="bounded_plan_complete",
                planner_model=plan.model,
                planner_input_tokens=plan.input_tokens,
                planner_output_tokens=plan.output_tokens,
            ),
        )
