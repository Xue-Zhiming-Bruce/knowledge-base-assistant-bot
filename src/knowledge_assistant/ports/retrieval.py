"""Retrieval-stage contracts."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from knowledge_assistant.domain.query import Evidence
from knowledge_assistant.domain.retrieval import QueryPlan, RetrievalStrategyName


class Reranker(Protocol):
    VERSION: str

    def rank(
        self,
        evidence: tuple[Evidence, ...],
        *,
        limit: int,
    ) -> tuple[Evidence, ...]:
        """Select and order evidence without inventing or modifying content."""


class EvidenceRetriever(Protocol):
    def retrieve(
        self,
        *,
        query_text: str,
        query_vector: tuple[float, ...] | None,
        embedding_model: str | None,
        dimensions: int | None,
        strategy: RetrievalStrategyName,
        limit: int,
        generation_id: UUID | None = None,
    ) -> tuple[Evidence, ...]:
        """Retrieve persistent chunk identities with a versioned strategy."""


class QueryPlanner(Protocol):
    VERSION: str

    def plan(self, question: str) -> QueryPlan:
        """Classify and decompose a question under a fixed call budget."""
