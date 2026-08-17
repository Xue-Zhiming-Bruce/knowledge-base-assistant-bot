"""Reviewer CLI demo: real RAG path (retrieval -> generation -> citations)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from knowledge_assistant.application.questions import QuestionService
from knowledge_assistant.application.retrieval import RetrievalOrchestrator
from knowledge_assistant.cli import build_parser
from knowledge_assistant.domain.query import (
    CitationValidator,
    Evidence,
    GeneratedAnswer,
)
from knowledge_assistant.domain.retrieval import DiversityReranker
from knowledge_assistant.ports.embeddings import EmbeddingBatch

SESSION_ID = uuid.uuid4()


def evidence() -> Evidence:
    return Evidence(
        citation_id="E1",
        chunk_id="chunk-1",
        document_id="doc_0123456789abcdef0123456789abcdef",
        revision_id="rev_0123456789abcdef0123456789abcdef",
        title="21 Lessons from 14 Years at Google",
        source_url="https://addyo.substack.com/p/21-lessons-from-14-years-at-google",
        vault_path="Articles/substack/21-lessons.md",
        heading_path=(),
        content="Engineers who thrive navigate people, politics, alignment, and ambiguity.",
        score=0.9,
    )


def second_evidence(citation_id: str = "E9") -> Evidence:
    return Evidence(
        citation_id=citation_id,
        chunk_id="chunk-9",
        document_id="doc_0123456789abcdef0123456789abcdef",
        revision_id="rev_0123456789abcdef0123456789abcdef",
        title="Another Lesson",
        source_url="https://addyo.substack.com/p/21-lessons-from-14-years-at-google",
        vault_path="Articles/substack/21-lessons.md",
        heading_path=(),
        content="A second supported fact about engineering craft.",
        score=0.8,
    )


class DemoRepository:
    def __init__(self, evidence_batch: tuple[Evidence, ...]) -> None:
        self._evidence = evidence_batch
        self.retrieval_calls = 0

    def retrieve(self, **_kwargs: object) -> tuple[Evidence, ...]:
        self.retrieval_calls += 1
        return self._evidence


class DemoEmbeddings:
    def embed(self, _texts: tuple[str, ...]) -> EmbeddingBatch:
        return EmbeddingBatch(((0.1, 0.2),), "embedding-test", 2, 3)


class DemoGenerator:
    PROMPT_VERSION = "grounded-answer-v1"

    def __init__(self) -> None:
        self.questions: list[str] = []
        self.evidence_counts: list[int] = []

    def generate(
        self,
        *,
        question: str,
        history: tuple[object, ...],
        evidence: tuple[Evidence, ...],
    ) -> GeneratedAnswer:
        assert question
        assert evidence, "generator must never be called without retrieved evidence"
        self.questions.append(question)
        self.evidence_counts.append(len(evidence))
        return GeneratedAnswer(
            answer="The engineers who thrive navigate people and ambiguity [E1].",
            citation_ids=("E1",),
            sufficient_evidence=True,
            model="generation-test",
            input_tokens=20,
            output_tokens=10,
        )


def demo_service(
    repository: DemoRepository,
    generator: DemoGenerator,
) -> QuestionService:
    return QuestionService(
        repository=repository,  # type: ignore[arg-type]
        retrieval=RetrievalOrchestrator(
            repository=repository,
            embeddings=DemoEmbeddings(),
            reranker=DiversityReranker(),
        ),
        generator=generator,
        validator=CitationValidator(),
        session_ttl_seconds=900,
    )


def test_demo_ask_uses_real_rag_path_without_bypassing_retrieval() -> None:
    repository = DemoRepository((evidence(),))
    generator = DemoGenerator()

    result = demo_service(repository, generator).ask_once(
        question="What do engineers actually need to get good at?"
    )

    assert repository.retrieval_calls == 1
    assert generator.evidence_counts == [1]
    assert len(generator.questions) == 1
    assert result.sufficient_evidence
    assert "[E1]" in result.rendered_text
    assert "Sources:" in result.rendered_text
    assert "addyo.substack.com" in result.rendered_text


def test_demo_ask_insufficient_evidence_does_not_call_generator() -> None:
    repository = DemoRepository(())
    generator = DemoGenerator()

    result = demo_service(repository, generator).ask_once(
        question="Does the knowledge base mention Pomodoro timers?"
    )

    assert result.sufficient_evidence is False
    assert "couldn't find enough relevant evidence" in result.rendered_text
    assert generator.evidence_counts == []
    assert result.citations == ()


def test_demo_ask_rejects_blank_and_oversized_questions() -> None:
    questions = demo_service(DemoRepository((evidence(),)), DemoGenerator())

    with pytest.raises(ValueError, match="blank"):
        questions.ask_once(question="   ")
    with pytest.raises(ValueError, match="4000"):
        questions.ask_once(question="x" * 4_001)


def test_demo_cli_parses_ingest_and_ask_subcommands() -> None:
    parser = build_parser()

    ask = parser.parse_args(
        ["demo", "ask", "--question", "What is RRF?", "--strategy", "weighted-hybrid-v1"]
    )
    assert ask.command == "demo"
    assert ask.demo_command == "ask"
    assert ask.question == "What is RRF?"
    assert ask.strategy == "weighted-hybrid-v1"

    ingest = parser.parse_args(["demo", "ingest", "--manifest", "data/sample/manifest.json"])
    assert ingest.demo_command == "ingest"
    assert str(ingest.manifest) == "data/sample/manifest.json"

    prefect = parser.parse_args(["prefect-ingest", "--manifest", "data/sample/manifest.json"])
    assert prefect.command == "prefect-ingest"
    assert prefect.recipient is None


def test_demo_cli_accepts_all_retrieval_strategies() -> None:
    parser = build_parser()
    for strategy in (
        "vector-only-v1",
        "lexical-only-v1",
        "weighted-hybrid-v1",
        "rrf-hybrid-v1",
        "agentic-decomposition-v1",
    ):
        args = parser.parse_args(["demo", "ask", "--question", "q", "--strategy", strategy])
        assert args.strategy == strategy


def test_centralized_generator_builder_selects_configured_version(tmp_path: Path) -> None:
    from knowledge_assistant.cli import _build_answer_generator
    from knowledge_assistant.config import Settings
    from knowledge_assistant.infrastructure.openai.answers import (
        OpenAIAnswerGenerator,
        OpenAIAnswerGeneratorV2,
    )

    base = {
        "KNOWLEDGE_ASSISTANT_ENVIRONMENT": "test",
        "KNOWLEDGE_ASSISTANT_VAULT_PATH": str(tmp_path / "vault"),
        "KNOWLEDGE_ASSISTANT_DATABASE_URL": (
            "postgresql://knowledge_assistant:secret@localhost/knowledge_assistant"
        ),
        "OPENAI_API_KEY": "key",
        "KNOWLEDGE_ASSISTANT_GENERATION_MODEL": "gpt-test",
    }

    default_v2 = _build_answer_generator(Settings.from_environment(base))
    assert isinstance(default_v2, OpenAIAnswerGeneratorV2)

    base["KNOWLEDGE_ASSISTANT_ANSWER_PROMPT_VERSION"] = "grounded-answer-v1"
    explicit_v1 = _build_answer_generator(Settings.from_environment(base))
    assert isinstance(explicit_v1, OpenAIAnswerGenerator)


def test_context_policy_rejects_invalid_limits() -> None:
    from knowledge_assistant.domain.query import ContextPolicy, bound_evidence

    with pytest.raises(ValueError, match="positive"):
        ContextPolicy(total_limit=0, per_item_limit=1)
    with pytest.raises(ValueError, match="per_item_limit"):
        ContextPolicy(total_limit=1, per_item_limit=2)

    # Empty/whitespace content is skipped, and the budget stops further items.
    blank = Evidence(
        citation_id="E1",
        chunk_id="c1",
        document_id="doc_0123456789abcdef0123456789abcdef",
        revision_id="rev_0123456789abcdef0123456789abcdef",
        title="t",
        source_url="https://example.com/s",
        vault_path="Articles/t.md",
        heading_path=(),
        content="   ",
        score=0.1,
    )
    assert bound_evidence((blank,), policy=ContextPolicy(total_limit=100, per_item_limit=10)) == ()
    assert bound_evidence((), policy=ContextPolicy(total_limit=100, per_item_limit=10)) == ()


def test_citation_validator_rejects_undeclared_markers() -> None:
    from knowledge_assistant.domain.query import AnswerValidationError

    validator = CitationValidator()
    # E9 must be available evidence so validation reaches the marker-declared check.
    evidence_pool = (evidence(), second_evidence(citation_id="E9"))
    undeclared_marker = GeneratedAnswer(
        answer="Claim [E9].",
        citation_ids=("E1",),
        sufficient_evidence=True,
        model="m",
        input_tokens=1,
        output_tokens=1,
    )
    with pytest.raises(AnswerValidationError, match="markers must be included"):
        validator.validate(undeclared_marker, evidence_pool)
