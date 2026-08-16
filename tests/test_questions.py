from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest

from knowledge_assistant.application.questions import QuestionService
from knowledge_assistant.application.retrieval import RetrievalOrchestrator
from knowledge_assistant.domain.query import (
    AnswerValidationError,
    CitationValidator,
    ConversationTurn,
    Evidence,
    GeneratedAnswer,
    NoActiveSessionError,
)
from knowledge_assistant.domain.retrieval import DiversityReranker
from knowledge_assistant.infrastructure.openai.answers import OpenAIAnswerGenerator
from knowledge_assistant.infrastructure.postgres.question_repository import (
    SessionStart,
    StoredTurn,
)
from knowledge_assistant.ports.embeddings import EmbeddingBatch


def evidence(citation_id: str = "E1") -> Evidence:
    return Evidence(
        citation_id=citation_id,
        chunk_id=f"chunk-{citation_id}",
        document_id="doc_0123456789abcdef0123456789abcdef",
        revision_id="rev_0123456789abcdef0123456789abcdef",
        title="Reasoning Notes",
        source_url="https://example.medium.com/reasoning",
        vault_path="Articles/medium/reasoning.md",
        heading_path=("Control",),
        content="Reasoning effort can be adjusted to balance quality and cost.",
        score=0.9,
    )


class FakeQuestionRepository:
    def __init__(self) -> None:
        self.session_id: uuid.UUID | None = uuid.uuid4()
        self.evidence: tuple[Evidence, ...] = (evidence(),)
        self.cached: StoredTurn | None = None
        self.recorded: list[dict[str, object]] = []
        self.ended = False

    def start_session(self, **_kwargs: object) -> SessionStart:
        assert self.session_id is not None
        return SessionStart(self.session_id, True)

    def end_session(self, _principal_id: str) -> bool:
        self.ended = True
        return True

    def active_session(self, _principal_id: str) -> uuid.UUID | None:
        return self.session_id

    def cleanup_expired(self) -> int:
        return 2

    def find_turn(self, **_kwargs: str) -> StoredTurn | None:
        return self.cached

    def history(self, _session_id: uuid.UUID) -> tuple[ConversationTurn, ...]:
        return (ConversationTurn("Earlier?", "Earlier answer [E1]."),)

    def retrieve(self, **_kwargs: object) -> tuple[Evidence, ...]:
        return self.evidence

    def record_turn(self, **kwargs: object) -> None:
        self.recorded.append(kwargs)

    def active_generation_id(self) -> str | None:
        return "generation-test"


class FakeEmbeddingProvider:
    def embed(self, _texts: tuple[str, ...]) -> EmbeddingBatch:
        return EmbeddingBatch(((0.1, 0.2),), "embedding-test", 2, 3)


class FakeAnswerGenerator:
    PROMPT_VERSION = "grounded-answer-v1"

    def __init__(self, generated: GeneratedAnswer | None = None) -> None:
        self.generated = generated or GeneratedAnswer(
            answer="It can balance quality and cost [E1].",
            citation_ids=("E1",),
            sufficient_evidence=True,
            model="generation-test",
            input_tokens=20,
            output_tokens=10,
        )

    def generate(self, **_kwargs: object) -> GeneratedAnswer:
        return self.generated


def service(repository: FakeQuestionRepository) -> QuestionService:
    return QuestionService(
        repository=cast(Any, repository),
        retrieval=RetrievalOrchestrator(
            repository=cast(Any, repository),
            embeddings=FakeEmbeddingProvider(),
            reranker=DiversityReranker(),
        ),
        generator=FakeAnswerGenerator(),
        validator=CitationValidator(),
        session_ttl_seconds=900,
    )


def test_question_service_lifecycle() -> None:
    repository = FakeQuestionRepository()
    questions = service(repository)

    assert questions.start("telegram:7")
    assert questions.is_active("telegram:7")
    assert questions.cleanup_expired() == 2
    assert questions.end("telegram:7")
    assert repository.ended


def test_question_service_answers_and_renders_sources() -> None:
    repository = FakeQuestionRepository()

    result = service(repository).answer(
        principal_id="telegram:7",
        client_message_id="10",
        question="How is reasoning effort useful?",
    )

    assert result.sufficient_evidence
    assert "[E1]" in result.rendered_text
    assert "Sources:" in result.rendered_text
    assert repository.recorded[0]["client_message_id"] == "10"


def test_question_service_returns_insufficient_evidence_without_generation() -> None:
    repository = FakeQuestionRepository()
    repository.evidence = ()

    result = service(repository).answer(
        principal_id="telegram:7",
        client_message_id="11",
        question="What is absent?",
    )

    assert not result.sufficient_evidence
    assert "enough relevant evidence" in result.rendered_text


def test_question_service_replays_cached_turn() -> None:
    repository = FakeQuestionRepository()
    repository.cached = StoredTurn("Cached answer", ({"citation_id": "E1"},), "model")

    result = service(repository).answer(
        principal_id="telegram:7",
        client_message_id="12",
        question="Duplicate",
    )

    assert result.rendered_text == "Cached answer"
    assert repository.recorded == []


def test_question_service_requires_active_session_and_valid_question() -> None:
    repository = FakeQuestionRepository()
    repository.session_id = None
    questions = service(repository)

    with pytest.raises(NoActiveSessionError):
        questions.answer(
            principal_id="telegram:7",
            client_message_id="13",
            question="Question",
        )
    with pytest.raises(ValueError, match="blank"):
        questions.answer(
            principal_id="telegram:7",
            client_message_id="14",
            question=" ",
        )
    with pytest.raises(ValueError, match="4000"):
        questions.answer(
            principal_id="telegram:7",
            client_message_id="15",
            question="x" * 4_001,
        )


def test_citation_validator_rejects_unknown_or_missing_citations() -> None:
    validator = CitationValidator()
    with pytest.raises(AnswerValidationError, match="unknown"):
        validator.validate(
            GeneratedAnswer("Claim [E9].", ("E9",), True, "model", None, None),
            (evidence(),),
        )
    with pytest.raises(AnswerValidationError, match="declare"):
        validator.validate(
            GeneratedAnswer("Claim.", (), True, "model", None, None),
            (evidence(),),
        )


def test_diversity_reranker_limits_one_document() -> None:
    first = evidence("E1")
    same_document = Evidence(
        citation_id="E2",
        chunk_id="chunk-E2",
        document_id=first.document_id,
        revision_id=first.revision_id,
        title=first.title,
        source_url=first.source_url,
        vault_path=first.vault_path,
        heading_path=(),
        content="More",
        score=0.8,
    )

    ranked = DiversityReranker(max_chunks_per_document=1).rank(
        (same_document, first),
        limit=2,
    )

    assert ranked == (first,)
    with pytest.raises(ValueError, match="positive"):
        DiversityReranker(max_chunks_per_document=0)
    with pytest.raises(ValueError, match="positive"):
        DiversityReranker().rank((first,), limit=0)


class FakeResponses:
    def parse(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            output_parsed=SimpleNamespace(
                answer="Grounded [E1].",
                citation_ids=["E1"],
                sufficient_evidence=True,
            ),
            usage=SimpleNamespace(input_tokens=30, output_tokens=8),
            model="generation-test",
        )


def test_openai_answer_adapter_maps_structured_response() -> None:
    client = SimpleNamespace(responses=FakeResponses())
    generator = OpenAIAnswerGenerator(
        api_key="unused",
        model="generation-test",
        client=cast(Any, client),
    )

    result = generator.generate(
        question="Question?",
        history=(ConversationTurn("Earlier?", "Answer."),),
        evidence=(evidence(),),
    )

    assert result.answer == "Grounded [E1]."
    assert result.citation_ids == ("E1",)
    assert result.input_tokens == 30
