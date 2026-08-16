"""Knowledge Engine orchestration for temporary grounded question sessions."""

from __future__ import annotations

import logging
import uuid
from time import perf_counter

from knowledge_assistant.application.retrieval import RetrievalOrchestrator
from knowledge_assistant.domain.query import (
    AnswerResult,
    CitationValidator,
    ContextPolicy,
    Evidence,
    FeedbackResult,
    NoActiveSessionError,
    bound_evidence,
)
from knowledge_assistant.domain.retrieval import RetrievalStrategyName, RetrievalTrace
from knowledge_assistant.infrastructure.postgres.question_repository import (
    PostgresQuestionRepository,
)
from knowledge_assistant.ports.answers import AnswerGenerator
from knowledge_assistant.ports.telemetry import NoOpTelemetry, Telemetry


class QuestionService:
    """Own Question Mode, retrieval, grounding, citations, and temporary history."""

    def __init__(
        self,
        *,
        repository: PostgresQuestionRepository,
        retrieval: RetrievalOrchestrator,
        generator: AnswerGenerator,
        validator: CitationValidator,
        session_ttl_seconds: int,
        retrieval_strategy: RetrievalStrategyName = (RetrievalStrategyName.WEIGHTED_HYBRID),
        telemetry: Telemetry | None = None,
    ) -> None:
        self._repository = repository
        self._retrieval = retrieval
        self._generator = generator
        self._validator = validator
        self._session_ttl_seconds = session_ttl_seconds
        self._retrieval_strategy = retrieval_strategy
        self._telemetry = telemetry or NoOpTelemetry()
        self._logger = logging.getLogger(__name__)

    def start(self, principal_id: str) -> bool:
        return self._repository.start_session(
            principal_id=principal_id,
            ttl_seconds=self._session_ttl_seconds,
        ).created

    def end(self, principal_id: str) -> bool:
        return self._repository.end_session(principal_id)

    def is_active(self, principal_id: str) -> bool:
        return self._repository.active_session(principal_id) is not None

    def cleanup_expired(self) -> int:
        deleted = self._repository.cleanup_expired()
        if deleted:
            self._logger.info("question_sessions_expired count=%s", deleted)
        return deleted

    def ask_once(self, *, question: str) -> AnswerResult:
        """Run the real RAG path once without a session (reviewer CLI demo).

        No session, history, or persistence: retrieve from the knowledge base,
        bound the context, generate a grounded answer, and validate citations.
        """

        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("question must not be blank")
        if len(normalized_question) > 4_000:
            raise ValueError("question must not exceed 4000 characters")
        with self._telemetry.span(
            "question.ask_once",
            {"retrieval.version": self._retrieval_strategy.value},
        ):
            retrieval = self._retrieval.retrieve(
                normalized_question,
                strategy=self._retrieval_strategy,
            )
            evidence = self._bounded_evidence(retrieval.evidence)
        if not evidence:
            return AnswerResult(
                rendered_text=(
                    "I couldn't find enough relevant evidence in your knowledge base "
                    "to answer that question."
                ),
                citations=(),
                sufficient_evidence=False,
                model="none",
            )
        generated = self._generator.generate(
            question=normalized_question,
            history=(),
            evidence=evidence,
        )
        citations = self._validator.validate(generated, evidence)
        return AnswerResult(
            rendered_text=self._render(generated.answer, citations),
            citations=citations,
            sufficient_evidence=generated.sufficient_evidence,
            model=generated.model,
        )

    def answer(
        self,
        *,
        principal_id: str,
        client_message_id: str,
        question: str,
    ) -> AnswerResult:
        started = perf_counter()
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("question must not be blank")
        if len(normalized_question) > 4_000:
            raise ValueError("question must not exceed 4000 characters")

        cached = self._repository.find_turn(
            principal_id=principal_id,
            client_message_id=client_message_id,
        )
        if cached is not None:
            return AnswerResult(
                rendered_text=cached.answer,
                citations=(),
                sufficient_evidence=bool(cached.citations),
                model=cached.model,
            )

        session_id = self._repository.active_session(principal_id)
        if session_id is None:
            raise NoActiveSessionError("Start Question Mode with /answer first.")
        history = self._repository.history(session_id)
        with self._telemetry.span(
            "question.answer",
            {"retrieval.version": self._retrieval_strategy.value},
        ):
            retrieval = self._retrieval.retrieve(
                normalized_question,
                strategy=self._retrieval_strategy,
            )
            evidence = self._bounded_evidence(retrieval.evidence)

        if not evidence:
            result = AnswerResult(
                rendered_text=(
                    "I couldn't find enough relevant evidence in your knowledge base "
                    "to answer that question."
                ),
                citations=(),
                sufficient_evidence=False,
                model="none",
            )
            self._record(
                session_id=session_id,
                client_message_id=client_message_id,
                question=normalized_question,
                result=result,
                retrieval_trace=retrieval.trace,
            )
            self._telemetry.count("questions_total", attributes={"outcome": "insufficient"})
            return result

        with self._telemetry.span("question.answer_generation"):
            generated = self._generator.generate(
                question=normalized_question,
                history=history,
                evidence=evidence,
            )
        citations = self._validator.validate(generated, evidence)
        rendered = self._render(generated.answer, citations)
        result = AnswerResult(
            rendered_text=rendered,
            citations=citations,
            sufficient_evidence=generated.sufficient_evidence,
            model=generated.model,
        )
        self._record(
            session_id=session_id,
            client_message_id=client_message_id,
            question=normalized_question,
            result=result,
            retrieval_trace=retrieval.trace,
        )
        self._telemetry.count("questions_total", attributes={"outcome": "success"})
        self._telemetry.count(
            "citations_total",
            value=len(citations),
            attributes={"outcome": "valid"},
        )
        self._telemetry.observe(
            "question_stage_duration_seconds",
            perf_counter() - started,
            {"stage": "total", "outcome": "success"},
        )
        self._logger.info(
            "question_answered session_id=%s citations=%s generation_model=%s "
            "retrieval_strategy=%s route=%s retrieval_rounds=%s "
            "generation_input_tokens=%s generation_output_tokens=%s",
            session_id,
            len(citations),
            generated.model,
            retrieval.trace.strategy.value,
            retrieval.trace.route.value,
            retrieval.trace.retrieval_rounds,
            generated.input_tokens,
            generated.output_tokens,
        )
        return result

    def _record(
        self,
        *,
        session_id: uuid.UUID,
        client_message_id: str,
        question: str,
        result: AnswerResult,
        retrieval_trace: RetrievalTrace,
    ) -> None:
        citation_data: tuple[dict[str, object], ...] = tuple(
            {
                "citation_id": item.citation_id,
                "chunk_id": item.chunk_id,
                "document_id": item.document_id,
                "revision_id": item.revision_id,
                "title": item.title,
                "source_url": item.source_url,
                "vault_path": item.vault_path,
            }
            for item in result.citations
        )
        self._repository.record_turn(
            session_id=session_id,
            client_message_id=client_message_id,
            question=question,
            answer=result.rendered_text,
            citations=citation_data,
            pipeline_version={
                "retrieval": retrieval_trace.strategy.value,
                "retrieval_route": retrieval_trace.route.value,
                "retrieval_rounds": retrieval_trace.retrieval_rounds,
                "retrieval_stop_reason": retrieval_trace.stop_reason,
                "citation_validator": self._validator.VERSION,
                "generation_model": result.model,
                "answer_prompt_version": self._generator.PROMPT_VERSION,
                "projection_generation": self._repository.active_generation_id()
                or "unknown",
            },
            ttl_seconds=self._session_ttl_seconds,
        )

    def record_answer_message_id(
        self,
        *,
        principal_id: str,
        client_message_id: str,
        answer_message_id: str,
    ) -> None:
        """Link a sent answer message so reply-based feedback can target the turn."""

        self._repository.record_answer_message_id(
            principal_id=principal_id,
            client_message_id=client_message_id,
            answer_message_id=answer_message_id,
        )

    def feedback(
        self,
        *,
        principal_id: str,
        direction: str,
        reply_to_message_id: int | None = None,
        answer_message_id: int | None = None,
    ) -> FeedbackResult:
        """Record privacy-safe feedback for the relevant answer turn (idempotent)."""

        if direction not in ("up", "down"):
            raise ValueError("feedback direction must be up or down")
        turn = self._repository.feedback_turn(
            principal_id=principal_id,
            client_message_id=(
                str(reply_to_message_id) if reply_to_message_id is not None else None
            ),
            answer_message_id=(
                str(answer_message_id) if answer_message_id is not None else None
            ),
        )
        if turn is None:
            return FeedbackResult(status="no_turn")
        pipeline = turn.pipeline_version
        created = self._repository.record_feedback(
            principal_id=principal_id,
            session_id=turn.session_id,
            turn_number=turn.turn_number,
            direction=direction,
            retrieval_strategy=str(pipeline.get("retrieval", "unknown")),
            projection_generation=str(pipeline.get("projection_generation", "unknown")),
            generation_model=str(pipeline.get("generation_model", "unknown")),
            answer_prompt_version=str(pipeline.get("answer_prompt_version", "unknown")),
        )
        outcome = "recorded" if created else "duplicate"
        self._telemetry.count(
            "feedback_total",
            attributes={"direction": direction, "outcome": outcome},
        )
        return FeedbackResult(
            status=outcome,
            session_id=turn.session_id,
            turn_number=turn.turn_number,
            direction=direction,
        )

    @staticmethod
    def _bounded_evidence(evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
        return bound_evidence(
            evidence,
            policy=ContextPolicy(total_limit=16_000, per_item_limit=2_400),
        )

    @staticmethod
    def _render(answer: str, citations: tuple[Evidence, ...]) -> str:
        text = answer.strip()
        if not citations:
            return text
        sources = "\n".join(
            f"[{item.citation_id}] {item.title} — {item.source_url}" for item in citations
        )
        return f"{text}\n\nSources:\n{sources}"
