"""Answer feedback service, bot command handling, and privacy guarantees."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest

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
from knowledge_assistant.infrastructure.postgres.question_repository import (
    SessionStart,
    StoredTurn,
)
from knowledge_assistant.infrastructure.telegram.client import (
    TelegramClient,
    TelegramMessage,
    TelegramUpdate,
)
from knowledge_assistant.ports.embeddings import EmbeddingBatch

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
