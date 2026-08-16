"""Deterministic retrieval policies."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from knowledge_assistant.domain.query import Evidence


class RetrievalStrategyName(StrEnum):
    VECTOR_ONLY = "vector-only-v1"
    LEXICAL_ONLY = "lexical-only-v1"
    WEIGHTED_HYBRID = "weighted-hybrid-v1"
    RRF_HYBRID = "rrf-hybrid-v1"
    AGENTIC_DECOMPOSITION = "agentic-decomposition-v1"


class QueryRoute(StrEnum):
    SIMPLE = "simple"
    COMPLEX = "complex"


@dataclass(frozen=True, slots=True)
class QueryPlan:
    route: QueryRoute
    subqueries: tuple[str, ...]
    reason: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.subqueries:
            raise ValueError("query plan must contain at least one subquery")
        if len(self.subqueries) > 3:
            raise ValueError("query plan must not contain more than three subqueries")
        if any(not query.strip() for query in self.subqueries):
            raise ValueError("query plan contains a blank subquery")


@dataclass(frozen=True, slots=True)
class RetrievalTrace:
    strategy: RetrievalStrategyName
    route: QueryRoute
    subqueries: tuple[str, ...]
    retrieval_rounds: int
    stop_reason: str
    planner_model: str | None = None
    planner_input_tokens: int | None = None
    planner_output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    evidence: tuple[Evidence, ...]
    trace: RetrievalTrace


class ReciprocalRankFusion:
    """Fuse independent rankings without assuming comparable raw scores."""

    VERSION = RetrievalStrategyName.RRF_HYBRID.value

    def __init__(self, *, rank_constant: int = 60) -> None:
        if rank_constant < 1:
            raise ValueError("rank_constant must be positive")
        self._rank_constant = rank_constant

    def fuse(
        self,
        rankings: tuple[tuple[Evidence, ...], ...],
        *,
        limit: int,
    ) -> tuple[Evidence, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        scores: dict[str, float] = {}
        candidates: dict[str, Evidence] = {}
        for ranking in rankings:
            for rank, item in enumerate(ranking, start=1):
                candidates.setdefault(item.chunk_id, item)
                scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + (
                    1 / (self._rank_constant + rank)
                )
        ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]
        return relabel_evidence(
            tuple(_with_score(candidates[chunk_id], scores[chunk_id]) for chunk_id in ordered)
        )


class DiversityReranker:
    """Preserve relevance while limiting domination by one document."""

    VERSION = "score-diversity-v1"

    def __init__(self, *, max_chunks_per_document: int = 3) -> None:
        if max_chunks_per_document < 1:
            raise ValueError("max_chunks_per_document must be positive")
        self._max_chunks_per_document = max_chunks_per_document

    def rank(
        self,
        evidence: tuple[Evidence, ...],
        *,
        limit: int,
    ) -> tuple[Evidence, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        counts: Counter[str] = Counter()
        selected: list[Evidence] = []
        for item in sorted(evidence, key=lambda candidate: (-candidate.score, candidate.chunk_id)):
            if counts[item.document_id] >= self._max_chunks_per_document:
                continue
            selected.append(item)
            counts[item.document_id] += 1
            if len(selected) == limit:
                break
        return tuple(selected)


def relabel_evidence(evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    """Assign query-local labels only after final evidence ordering."""

    return tuple(
        Evidence(
            citation_id=f"E{index}",
            chunk_id=item.chunk_id,
            document_id=item.document_id,
            revision_id=item.revision_id,
            title=item.title,
            source_url=item.source_url,
            vault_path=item.vault_path,
            heading_path=item.heading_path,
            content=item.content,
            score=item.score,
        )
        for index, item in enumerate(evidence, start=1)
    )


def _with_score(item: Evidence, score: float) -> Evidence:
    return Evidence(
        citation_id=item.citation_id,
        chunk_id=item.chunk_id,
        document_id=item.document_id,
        revision_id=item.revision_id,
        title=item.title,
        source_url=item.source_url,
        vault_path=item.vault_path,
        heading_path=item.heading_path,
        content=item.content,
        score=score,
    )
