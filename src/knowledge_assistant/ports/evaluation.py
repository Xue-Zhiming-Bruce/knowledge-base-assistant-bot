"""Synthetic evaluation provider contracts."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from knowledge_assistant.domain.evaluation import (
    AnswerJudgeResult,
    EvaluationChunk,
    GeneratedQuestion,
)


class EvaluationCorpus(Protocol):
    def active_generation_id(self) -> UUID:
        """Return the active projection generation."""

    def sample_chunks(
        self,
        *,
        generation_id: UUID,
        count: int,
        seed: str,
        minimum_tokens: int = 80,
        maximum_tokens: int = 500,
        max_per_document: int = 2,
    ) -> tuple[EvaluationChunk, ...]:
        """Select stable current-revision chunks without database randomness."""

    def validate_chunk(
        self,
        *,
        generation_id: UUID,
        chunk_id: str,
        content_fingerprint: str,
    ) -> bool:
        """Fail closed when a frozen case no longer resolves exactly."""

    def document_chunks(
        self,
        *,
        generation_id: UUID,
        document_id: str,
    ) -> tuple[str, ...]:
        """Return chunk ids of the current revision of one document in a generation."""

    def document_id_for_url(self, *, url: str) -> str | None:
        """Resolve a canonical source URL to its current document id, if ingested."""


class SyntheticQuestionGenerator(Protocol):
    PROMPT_VERSION: str

    def generate(self, chunk: EvaluationChunk) -> GeneratedQuestion:
        """Create one standalone question from one selected chunk."""


class QuestionNaturalizer(Protocol):
    """Rewrite a generated question without seeing the source chunk."""

    PROMPT_VERSION: str

    @property
    def model(self) -> str:
        """Return the model used for the source-blind rewrite."""

    def naturalize(self, question: str) -> str:
        """Return a natural-language rewrite that preserves intent."""


class AnswerJudge(Protocol):
    """Score a grounded answer against a fixed versioned rubric."""

    PROMPT_VERSION: str
    RUBRIC_VERSION: str

    @property
    def model(self) -> str:
        """Return the model used for judging."""

    def judge(
        self,
        *,
        question: str,
        answer: str,
        reference_answer: str,
        required_facts: tuple[str, ...],
        supporting_excerpt: str,
        no_answer: bool,
    ) -> AnswerJudgeResult:
        """Return bounded structured dimension scores."""
