"""Answer feedback service, bot command handling, and privacy guarantees."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import Any, cast

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg.rows import dict_row

from knowledge_assistant.application.bot import TelegramPollingService
from knowledge_assistant.application.questions import QuestionService
from knowledge_assistant.application.retrieval import RetrievalOrchestrator
from knowledge_assistant.domain.query import (
    CitationValidator,
    ConversationTurn,
    Evidence,
    FeedbackTurn,
    GeneratedAnswer,
)
from knowledge_assistant.domain.retrieval import DiversityReranker
from knowledge_assistant.domain.sources import SourceClassifier
from knowledge_assistant.infrastructure.postgres.migrations import MigrationRunner
from knowledge_assistant.infrastructure.postgres.question_repository import (
    PostgresQuestionRepository,
    SessionStart,
    StoredTurn,
)
from knowledge_assistant.infrastructure.telegram.client import (
    TelegramClient,
    TelegramMessage,
    TelegramUpdate,
)
from knowledge_assistant.ports.embeddings import EmbeddingBatch

load_dotenv()

SESSION_ID = uuid.uuid4()
TURN_NUMBER = 3
PIPELINE = {
    "retrieval": "weighted-hybrid-v1",
    "retrieval_route": "simple",
    "retrieval_rounds": 1,
    "retrieval_stop_reason": "single_pass_complete",
    "citation_validator": "deterministic-citations-v1",
    "generation_model": "generation-model",
    "answer_prompt_version": "grounded-answer-v1",
    "projection_generation": str(uuid.uuid4()),
}


class FakeFeedbackRepository:
    def __init__(self) -> None:
        self.feedback_calls: list[dict[str, object]] = []
        self.turn: FeedbackTurn | None = FeedbackTurn(
            session_id=SESSION_ID,
            turn_number=TURN_NUMBER,
            pipeline_version=dict(PIPELINE),
        )
        self.lookup: dict[str, str] = {}
        self.created = True

    def active_generation_id(self) -> str | None:
        return str(PIPELINE["projection_generation"])

    def feedback_turn(
        self,
        *,
        principal_id: str,
        client_message_id: str | None,
        answer_message_id: str | None,
    ) -> FeedbackTurn | None:
        assert principal_id
        self.lookup["client_message_id"] = client_message_id or ""
        self.lookup["answer_message_id"] = answer_message_id or ""
        return self.turn

    def record_answer_message_id(self, **_kwargs: object) -> None:
        return None

    def record_feedback(self, **kwargs: object) -> bool:
        self.feedback_calls.append(kwargs)
        return self.created

    def start_session(self, **_kwargs: object) -> SessionStart:
        return SessionStart(SESSION_ID, True)

    def end_session(self, _principal_id: str) -> bool:
        return True

    def active_session(self, _principal_id: str) -> uuid.UUID | None:
        return SESSION_ID

    def cleanup_expired(self) -> int:
        return 0

    def find_turn(self, **_kwargs: str) -> StoredTurn | None:
        return None

    def history(self, _session_id: uuid.UUID) -> tuple[ConversationTurn, ...]:
        return ()

    def retrieve(self, **_kwargs: object) -> tuple[Evidence, ...]:
        return ()

    def record_turn(self, **_kwargs: object) -> None:
        return None


class FakeAnswerGenerator:
    PROMPT_VERSION = "grounded-answer-v1"

    def generate(self, **_kwargs: object) -> GeneratedAnswer:
        return GeneratedAnswer(
            answer="Grounded [E1].",
            citation_ids=("E1",),
            sufficient_evidence=True,
            model="generation-model",
            input_tokens=20,
            output_tokens=10,
        )


def service(repository: FakeFeedbackRepository) -> QuestionService:
    return QuestionService(
        repository=cast(Any, repository),
        retrieval=RetrievalOrchestrator(
            repository=cast(Any, repository),
            embeddings=_FakeEmbeddingProvider(),
            reranker=DiversityReranker(),
        ),
        generator=FakeAnswerGenerator(),
        validator=CitationValidator(),
        session_ttl_seconds=900,
    )


class _FakeEmbeddingProvider:
    def embed(self, _texts: tuple[str, ...]) -> EmbeddingBatch:
        return EmbeddingBatch(((0.1, 0.2),), "embedding-test", 2, 3)


def test_feedback_records_only_safe_metadata() -> None:
    repository = FakeFeedbackRepository()
    result = service(repository).feedback(
        principal_id="telegram:7",
        direction="up",
    )

    assert result.status == "recorded"
    assert result.session_id == SESSION_ID
    assert result.turn_number == TURN_NUMBER
    assert result.direction == "up"
    call = repository.feedback_calls[0]
    assert call["principal_id"] == "telegram:7"
    assert call["direction"] == "up"
    assert call["retrieval_strategy"] == "weighted-hybrid-v1"
    assert call["projection_generation"] == PIPELINE["projection_generation"]
    assert call["generation_model"] == "generation-model"
    assert call["answer_prompt_version"] == "grounded-answer-v1"
    assert all(
        keyword not in {str(value).lower() for value in call.values()}
        for keyword in ("question", "answer", "https://")
    )


def test_feedback_is_idempotent_for_duplicate_direction() -> None:
    repository = FakeFeedbackRepository()
    repository.created = False

    result = service(repository).feedback(principal_id="telegram:7", direction="down")

    assert result.status == "duplicate"
    assert len(repository.feedback_calls) == 1


def test_feedback_no_turn_returns_graceful_status() -> None:
    repository = FakeFeedbackRepository()
    repository.turn = None

    result = service(repository).feedback(principal_id="telegram:7", direction="up")

    assert result.status == "no_turn"
    assert repository.feedback_calls == []


def test_feedback_rejects_invalid_direction() -> None:
    repository = FakeFeedbackRepository()

    with pytest.raises(ValueError, match="direction"):
        service(repository).feedback(principal_id="telegram:7", direction="sideways")


def test_feedback_reply_targets_turn_by_message_id() -> None:
    repository = FakeFeedbackRepository()

    service(repository).feedback(
        principal_id="telegram:7",
        direction="up",
        reply_to_message_id=41,
        answer_message_id=77,
    )

    assert repository.lookup["client_message_id"] == "41"
    assert repository.lookup["answer_message_id"] == "77"


def test_answer_records_prompt_version_and_projection_generation() -> None:
    recorded: dict[str, object] = {}

    class RecordingRepository(FakeFeedbackRepository):
        def record_turn(self, **kwargs: object) -> None:
            recorded.update(kwargs)

    questions = service(RecordingRepository())
    questions.answer(
        principal_id="telegram:7",
        client_message_id="10",
        question="How is reasoning effort useful?",
    )

    pipeline = cast(dict[str, object], recorded["pipeline_version"])
    assert pipeline["answer_prompt_version"] == "grounded-answer-v1"
    assert pipeline["projection_generation"] == PIPELINE["projection_generation"]


class FakeBotQuestions:
    def __init__(self) -> None:
        self.feedback_calls: list[tuple[str, str, int | None]] = []
        self.started = 0
        self.ended = 0
        self.answer_error: Exception | None = None
        self.active = True

    def feedback(
        self,
        *,
        principal_id: str,
        direction: str,
        reply_to_message_id: int | None = None,
    ) -> SimpleNamespace:
        self.feedback_calls.append((principal_id, direction, reply_to_message_id))
        return SimpleNamespace(status="recorded")

    def record_answer_message_id(self, **_kwargs: object) -> None:
        return None

    def start(self, _principal_id: str) -> bool:
        self.started += 1
        return True

    def end(self, _principal_id: str) -> bool:
        self.ended += 1
        return True

    def is_active(self, _principal_id: str) -> bool:
        return self.active

    def cleanup_expired(self) -> int:
        return 0

    def answer(self, **_kwargs: object) -> SimpleNamespace:
        if self.answer_error is not None:
            raise self.answer_error
        return SimpleNamespace(rendered_text="Grounded answer [E1].")


def make_bot_with_deletions(deletions: object) -> TelegramPollingService:
    return TelegramPollingService(
        telegram=cast(Any, FakeBotTelegram()),
        repository=cast(Any, SimpleNamespace()),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
        questions=cast(Any, FakeBotQuestions()),
        deletions=cast(Any, deletions),
    )


def delete_result(
    *,
    deleted: bool = False,
    deleted_title: str | None = None,
    suggestions: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        deleted=deleted,
        deleted_title=deleted_title,
        suggestions=suggestions,
    )


class FakeBotTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> None:
        del chat_id, reply_to_message_id
        self.sent.append(text)


def bot_update(text: str, *, reply_to: int | None = None) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=42,
        message=TelegramMessage(
            message_id=5,
            chat_id=7,
            chat_type="private",
            sender_id=7,
            text=text,
            reply_to_message_id=reply_to,
        ),
    )


def make_bot(questions: FakeBotQuestions) -> TelegramPollingService:
    return TelegramPollingService(
        telegram=cast(Any, FakeBotTelegram()),
        repository=cast(Any, SimpleNamespace()),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
        questions=cast(Any, questions),
    )


def test_bot_feedback_up_and_down_commands() -> None:
    questions = FakeBotQuestions()
    service_with_questions = make_bot(questions)
    telegram = cast(FakeBotTelegram, service_with_questions._telegram)

    service_with_questions.process_update(bot_update("/feedback up", reply_to=41))
    service_with_questions.process_update(bot_update("/feedback down"))

    assert questions.feedback_calls[0] == ("telegram:7", "up", 41)
    assert questions.feedback_calls[1] == ("telegram:7", "down", None)
    assert telegram.sent[0] == "Thanks! Feedback recorded."
    assert telegram.sent[1] == "Thanks! Feedback recorded."


def test_bot_feedback_invalid_usage_and_unconfigured() -> None:
    questions = FakeBotQuestions()
    service_with_questions = make_bot(questions)
    service_with_questions.process_update(bot_update("/feedback sideways"))
    assert "Usage: /feedback up or /feedback down" in cast(
        FakeBotTelegram, service_with_questions._telegram
    ).sent[0]

    telegram = FakeBotTelegram()
    unconfigured = TelegramPollingService(
        telegram=cast(Any, telegram),
        repository=cast(Any, SimpleNamespace()),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
    )
    unconfigured.process_update(bot_update("/feedback up"))
    assert "not configured" in telegram.sent[0]


def test_bot_feedback_no_turn_and_duplicate_responses() -> None:
    class NoTurnQuestions(FakeBotQuestions):
        def feedback(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(status="no_turn")

    service_with_questions = make_bot(NoTurnQuestions())
    service_with_questions.process_update(bot_update("/feedback up"))
    assert "No previous answer" in cast(FakeBotTelegram, service_with_questions._telegram).sent[0]

    class DuplicateQuestions(FakeBotQuestions):
        def feedback(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(status="duplicate")

    service_with_questions = make_bot(DuplicateQuestions())
    service_with_questions.process_update(bot_update("/feedback up"))
    assert "already recorded" in cast(FakeBotTelegram, service_with_questions._telegram).sent[0]


def test_telegram_parses_reply_to_message_id() -> None:
    update = TelegramClient._parse_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 5,
                "chat": {"id": 7, "type": "private"},
                "from": {"id": 7},
                "text": "/feedback up",
                "reply_to_message": {"message_id": 41},
            },
        }
    )

    assert update.message is not None
    assert update.message.reply_to_message_id == 41


def test_telegram_send_message_returns_sent_message_id() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 99}},
            request=request,
        )

    client = TelegramClient(
        token="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.send_message(chat_id=7, text="answer", reply_to_message_id=5) == 99


class RecordingTelemetry:
    """Minimal telemetry recording feedback_total count calls."""

    def __init__(self) -> None:
        self.counts: list[tuple[str, int, dict[str, str]]] = []

    def count(
        self,
        name: str,
        value: int = 1,
        attributes: dict[str, str] | None = None,
    ) -> None:
        self.counts.append((name, value, attributes or {}))


def test_feedback_counts_grafana_metric_with_safe_labels() -> None:
    repository = FakeFeedbackRepository()
    telemetry = RecordingTelemetry()
    questions = QuestionService(
        repository=cast(Any, repository),
        retrieval=RetrievalOrchestrator(
            repository=cast(Any, repository),
            embeddings=_FakeEmbeddingProvider(),
            reranker=DiversityReranker(),
        ),
        generator=FakeAnswerGenerator(),
        validator=CitationValidator(),
        session_ttl_seconds=900,
        telemetry=cast(Any, telemetry),
    )

    questions.feedback(principal_id="telegram:7", direction="up")
    repository.created = False
    questions.feedback(principal_id="telegram:7", direction="up")

    feedback_counts = [call for call in telemetry.counts if call[0] == "feedback_total"]
    assert len(feedback_counts) == 2
    assert feedback_counts[0][2] == {"direction": "up", "outcome": "recorded"}
    assert feedback_counts[1][2] == {"direction": "up", "outcome": "duplicate"}
    assert all(len(call[2]) <= 2 for call in feedback_counts)


def test_feedback_survives_session_deletion_and_expiry() -> None:
    """Live-DB proof that feedback survives /end and expiry (skips without DB)."""

    database_url = os.environ.get("KNOWLEDGE_ASSISTANT_DATABASE_URL")
    if database_url is None:
        pytest.skip("KNOWLEDGE_ASSISTANT_DATABASE_URL not set (no live database)")
    try:
        MigrationRunner(database_url).apply()
    except Exception as error:
        pytest.skip(f"live feedback durability unavailable: {error}")

    principal = "durable-feedback-test"
    repository = PostgresQuestionRepository(database_url)
    sessions: list[uuid.UUID] = []
    try:
        # Session A: feedback must survive /end (session and turn deletion).
        session_a = repository.start_session(principal_id=principal, ttl_seconds=3600)
        sessions.append(session_a.session_id)
        repository.record_turn(
            session_id=session_a.session_id,
            client_message_id="durable-a-1",
            question="private question A?",
            answer="private answer A [E1].",
            citations=(),
            pipeline_version={
                "retrieval": "weighted-hybrid-v1",
                "generation_model": "generation-model",
                "answer_prompt_version": "grounded-answer-v2",
                "projection_generation": str(uuid.uuid4()),
            },
            ttl_seconds=3600,
        )
        assert repository.record_feedback(
            principal_id=principal,
            session_id=session_a.session_id,
            turn_number=1,
            direction="up",
            retrieval_strategy="weighted-hybrid-v1",
            projection_generation="gen-a",
            generation_model="generation-model",
            answer_prompt_version="grounded-answer-v2",
        )
        assert repository.end_session(principal)

        # Session B: feedback must survive expiry cleanup.
        session_b = repository.start_session(principal_id=principal, ttl_seconds=3600)
        sessions.append(session_b.session_id)
        repository.record_turn(
            session_id=session_b.session_id,
            client_message_id="durable-b-1",
            question="private question B?",
            answer="private answer B [E1].",
            citations=(),
            pipeline_version={
                "retrieval": "lexical-only-v1",
                "generation_model": "generation-model",
                "answer_prompt_version": "grounded-answer-v1",
                "projection_generation": str(uuid.uuid4()),
            },
            ttl_seconds=3600,
        )
        assert repository.record_feedback(
            principal_id=principal,
            session_id=session_b.session_id,
            turn_number=1,
            direction="down",
            retrieval_strategy="lexical-only-v1",
            projection_generation="gen-b",
            generation_model="generation-model",
            answer_prompt_version="grounded-answer-v1",
        )
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """
                UPDATE question_sessions
                SET expires_at = now() - interval '1 second'
                WHERE session_id = %s
                """,
                (session_b.session_id,),
            )
        assert repository.cleanup_expired() >= 1

        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            sessions_left = connection.execute(
                "SELECT count(*) FROM question_sessions WHERE principal_id = %s",
                (principal,),
            ).fetchone()
            turns_left = connection.execute(
                "SELECT count(*) FROM session_turns WHERE session_id = ANY(%s)",
                (sessions,),
            ).fetchone()
            feedback_rows = connection.execute(
                "SELECT * FROM answer_feedback WHERE principal_id = %s ORDER BY created_at",
                (principal,),
            ).fetchall()
        assert sessions_left is not None
        assert turns_left is not None
        assert sessions_left["count"] == 0  # temporary conversation content deleted
        assert turns_left["count"] == 0
        assert len(feedback_rows) == 2  # feedback survived both deletions

        # Privacy: retained columns are exactly the safe metadata set, and no
        # value carries question/answer/prompt/evidence/URL/credential content.
        safe_columns = {
            "feedback_id",
            "principal_id",
            "session_id",
            "turn_number",
            "direction",
            "retrieval_strategy",
            "projection_generation",
            "generation_model",
            "answer_prompt_version",
            "created_at",
        }
        for row in feedback_rows:
            assert set(row.keys()) == safe_columns
            joined = " ".join(str(value) for value in row.values()).lower()
            assert "private question" not in joined
            assert "private answer" not in joined
            assert "http" not in joined
            assert "key" not in joined

        # Idempotency survives deletion: the same opaque turn reference is a no-op.
        assert not repository.record_feedback(
            principal_id=principal,
            session_id=session_a.session_id,
            turn_number=1,
            direction="up",
            retrieval_strategy="weighted-hybrid-v1",
            projection_generation="gen-a",
            generation_model="generation-model",
            answer_prompt_version="grounded-answer-v2",
        )
        with psycopg.connect(database_url) as connection:
            feedback_after_duplicate = connection.execute(
                "SELECT count(*) FROM answer_feedback WHERE principal_id = %s",
                (principal,),
            ).fetchone()
        assert feedback_after_duplicate is not None
        assert feedback_after_duplicate[0] == 2
    finally:
        try:
            with psycopg.connect(database_url) as connection:
                connection.execute(
                    "DELETE FROM answer_feedback WHERE principal_id = %s",
                    (principal,),
                )
                connection.execute(
                    "DELETE FROM question_sessions WHERE principal_id = %s",
                    (principal,),
                )
        finally:
            repository.close()


class DeletingDeletions:
    def __init__(self, result: SimpleNamespace, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def delete_by_title(self, _title: str) -> SimpleNamespace:
        if self.error is not None:
            raise self.error
        return self.result


def test_bot_delete_command_matrix() -> None:
    usage_bot = make_bot(FakeBotQuestions())
    usage_bot.process_update(bot_update("/delete"))
    usage_reply = cast(FakeBotTelegram, usage_bot._telegram).sent[0]
    assert "Usage: /delete <exact article title>" in usage_reply

    unconfigured = TelegramPollingService(
        telegram=cast(Any, FakeBotTelegram()),
        repository=cast(Any, SimpleNamespace()),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
    )
    unconfigured.process_update(bot_update("/delete some title"))
    unconfigured_reply = cast(FakeBotTelegram, unconfigured._telegram).sent[0]
    assert "Article deletion is not configured." in unconfigured_reply

    success = make_bot_with_deletions(
        DeletingDeletions(delete_result(deleted=True, deleted_title="Exact Title"))
    )
    success.process_update(bot_update("/delete Exact Title"))
    assert "Deleted: Exact Title" in cast(FakeBotTelegram, success._telegram).sent[-1]

    suggested = make_bot_with_deletions(
        DeletingDeletions(delete_result(suggestions=("Alpha", "Beta")))
    )
    suggested.process_update(bot_update("/delete alpha"))
    last = cast(FakeBotTelegram, suggested._telegram).sent[-1]
    assert "Did you mean:" in last
    assert "- Alpha" in last
    assert "- Beta" in last

    no_match = make_bot_with_deletions(DeletingDeletions(delete_result()))
    no_match.process_update(bot_update("/delete nowhere"))
    assert "No article has that exact title" in cast(FakeBotTelegram, no_match._telegram).sent[-1]

    failed = make_bot_with_deletions(
        DeletingDeletions(delete_result(), error=RuntimeError("boom"))
    )
    failed.process_update(bot_update("/delete Exact Title"))
    failed_reply = cast(FakeBotTelegram, failed._telegram).sent[-1]
    assert "I couldn't delete that article safely" in failed_reply


def test_bot_answer_end_unconfigured_and_question_failure() -> None:
    unconfigured = TelegramPollingService(
        telegram=cast(Any, FakeBotTelegram()),
        repository=cast(Any, SimpleNamespace()),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
    )
    unconfigured.process_update(bot_update("/answer"))
    unconfigured_reply = cast(FakeBotTelegram, unconfigured._telegram).sent[0]
    assert "Question Mode is not configured." in unconfigured_reply
    unconfigured.process_update(bot_update("/end"))
    assert "Question Mode is not active." in cast(FakeBotTelegram, unconfigured._telegram).sent[1]

    failing = FakeBotQuestions()
    failing.answer_error = RuntimeError("generation failed")
    bot = make_bot(failing)
    bot.process_update(bot_update("What is RRF?"))
    failure_reply = cast(FakeBotTelegram, bot._telegram).sent[0]
    assert "I couldn't answer that question right now" in failure_reply


class SubmittingRepository:
    def __init__(self, created: bool = True) -> None:
        self.created = created
        self.submissions: list[dict[str, object]] = []

    def submit(self, **kwargs: object) -> SimpleNamespace:
        self.submissions.append(kwargs)
        return SimpleNamespace(created=self.created)


def test_bot_submits_source_url_and_help_paths() -> None:
    repository = SubmittingRepository(created=True)
    bot = TelegramPollingService(
        telegram=cast(Any, FakeBotTelegram()),
        repository=cast(Any, repository),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
        questions=cast(Any, FakeBotQuestions()),
    )
    bot.process_update(
        bot_update("https://addyo.substack.com/p/software-factories-light-and-dark")
    )
    assert "saving and indexing" in cast(FakeBotTelegram, bot._telegram).sent[0]
    assert repository.submissions[0]["recipient_key"] == "7"
    assert repository.submissions[0]["request_message_id"] == "5"

    duplicate = SubmittingRepository(created=False)
    dup_bot = TelegramPollingService(
        telegram=cast(Any, FakeBotTelegram()),
        repository=cast(Any, duplicate),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
        questions=cast(Any, FakeBotQuestions()),
    )
    dup_bot.process_update(bot_update("https://addyo.substack.com/p/software-factories-light-and-dark"))
    assert "already being processed" in cast(FakeBotTelegram, dup_bot._telegram).sent[0]

    unsupported = TelegramPollingService(
        telegram=cast(Any, FakeBotTelegram()),
        repository=cast(Any, SubmittingRepository()),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
        questions=cast(Any, FakeBotQuestions()),
    )
    unsupported.process_update(bot_update("https://example.com/random"))
    unsupported_reply = cast(FakeBotTelegram, unsupported._telegram).sent[0]
    assert "Only Medium, Substack, and rich X Article URLs" in unsupported_reply

    inactive = FakeBotQuestions()
    inactive.active = False
    help_bot = TelegramPollingService(
        telegram=cast(Any, FakeBotTelegram()),
        repository=cast(Any, SubmittingRepository()),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
        questions=cast(Any, inactive),
    )
    help_bot.process_update(bot_update("hello there"))
    assert "Send a Medium, Substack, or rich X Article URL" in cast(
        FakeBotTelegram, help_bot._telegram
    ).sent[0]
