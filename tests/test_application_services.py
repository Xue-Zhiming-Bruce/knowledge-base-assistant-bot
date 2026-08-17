from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from knowledge_assistant.application.assets import ArticleAssetMaterializer
from knowledge_assistant.application.bot import TelegramPollingService
from knowledge_assistant.application.worker import IngestionWorker
from knowledge_assistant.domain.chunks import MarkdownChunker
from knowledge_assistant.domain.documents import SourceProvider
from knowledge_assistant.domain.ingestion import IngestionState
from knowledge_assistant.domain.sources import (
    ExtractedArticle,
    ExtractedImage,
    FetchedContent,
    SourceClassifier,
    SourceFetchError,
)
from knowledge_assistant.infrastructure.http.safe_image_fetcher import FetchedImage
from knowledge_assistant.infrastructure.postgres.ingestion_repository import (
    ClaimedJob,
    JobSubmission,
    JobSubscriber,
    PendingNotification,
)
from knowledge_assistant.infrastructure.telegram.client import (
    TelegramApiError,
    TelegramMessage,
    TelegramUpdate,
)
from knowledge_assistant.infrastructure.vault.filesystem import FileSystemVaultRepository
from knowledge_assistant.ports.embeddings import EmbeddingBatch


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int | None]] = []

    def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> None:
        self.sent.append((chat_id, text, reply_to_message_id))


class FailingTelegram(FakeTelegram):
    def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> None:
        del chat_id, text, reply_to_message_id
        raise TelegramApiError("unavailable")


class FakeBotRepository:
    def __init__(self) -> None:
        self.submissions: list[str] = []

    def submit(self, **kwargs: Any) -> JobSubmission:
        self.submissions.append(cast(str, kwargs["idempotency_key"]))
        return JobSubmission(uuid.uuid4(), IngestionState.QUEUED, True)


class FakeQuestions:
    def __init__(self) -> None:
        self.active = False
        self.ended = False

    def start(self, _principal_id: str) -> bool:
        self.active = True
        return True

    def end(self, _principal_id: str) -> bool:
        self.active = False
        self.ended = True
        return True

    def is_active(self, _principal_id: str) -> bool:
        return self.active

    def cleanup_expired(self) -> int:
        return 0

    def answer(self, **_kwargs: str) -> SimpleNamespace:
        return SimpleNamespace(rendered_text="Grounded answer [E1].")


class FakeDeletions:
    def delete_by_title(self, title: str) -> SimpleNamespace:
        if title == "Exact Article":
            return SimpleNamespace(
                deleted=True,
                deleted_title="Exact Article",
                suggestions=(),
            )
        return SimpleNamespace(
            deleted=False,
            deleted_title=None,
            suggestions=("Exact Article",),
        )


class LoopBotRepository(FakeBotRepository):
    def __init__(self) -> None:
        super().__init__()
        self.checkpoint: int | None = None
        self.heartbeats = 0

    def get_checkpoint(self, _key: str) -> None:
        return None

    def heartbeat(self, **_kwargs: str) -> None:
        self.heartbeats += 1

    def set_checkpoint(self, _key: str, value: int) -> None:
        self.checkpoint = value


def update(text: str, *, sender: int = 7) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=42,
        message=TelegramMessage(
            message_id=5,
            chat_id=7,
            chat_type="private",
            sender_id=sender,
            text=text,
        ),
    )


def test_bot_submits_supported_url_and_acknowledges() -> None:
    telegram = FakeTelegram()
    repository = FakeBotRepository()
    service = TelegramPollingService(
        telegram=cast(Any, telegram),
        repository=cast(Any, repository),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
    )

    service.process_update(update("Read https://writer.substack.com/p/good?utm_source=x"))

    assert repository.submissions == ["telegram:update:42"]
    assert "saving and indexing" in telegram.sent[0][1]


def test_bot_rejects_unknown_user_and_explains_invalid_input() -> None:
    telegram = FakeTelegram()
    repository = FakeBotRepository()
    service = TelegramPollingService(
        telegram=cast(Any, telegram),
        repository=cast(Any, repository),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
    )

    service.process_update(update("https://medium.com/private", sender=99))
    service.process_update(update("hello"))
    service.process_update(update("https://example.com/article"))

    assert repository.submissions == []
    assert len(telegram.sent) == 2


def test_bot_question_mode_commands_and_question() -> None:
    telegram = FakeTelegram()
    questions = FakeQuestions()
    service = TelegramPollingService(
        telegram=cast(Any, telegram),
        repository=cast(Any, FakeBotRepository()),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
        questions=cast(Any, questions),
    )

    service.process_update(update("/answer"))
    service.process_update(update("What did I save?"))
    service.process_update(update("/end"))

    assert "Question Mode started" in telegram.sent[0][1]
    assert telegram.sent[1][1] == "Grounded answer [E1]."
    assert "history was deleted" in telegram.sent[2][1]
    assert questions.ended


def test_bot_delete_requires_title_and_reports_exact_or_suggested_match() -> None:
    telegram = FakeTelegram()
    service = TelegramPollingService(
        telegram=cast(Any, telegram),
        repository=cast(Any, FakeBotRepository()),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
        deletions=cast(Any, FakeDeletions()),
    )

    service.process_update(update("/delete"))
    service.process_update(update("/delete Article"))
    service.process_update(update("/delete Exact Article"))

    assert telegram.sent[0][1] == "Usage: /delete <exact article title>"
    assert "Nothing was deleted" in telegram.sent[1][1]
    assert "Exact Article" in telegram.sent[1][1]
    assert telegram.sent[2][1] == "Deleted: Exact Article"


def test_bot_poll_loop_persists_checkpoint() -> None:
    telegram = FakeTelegram()
    repository = LoopBotRepository()
    service = TelegramPollingService(
        telegram=cast(Any, telegram),
        repository=cast(Any, repository),
        classifier=SourceClassifier(),
        allowed_user_ids=frozenset({7}),
        poll_timeout_seconds=1,
    )

    def get_updates(**_kwargs: object) -> tuple[TelegramUpdate, ...]:
        service.stop()
        return (update("https://medium.com/story"),)

    telegram.get_updates = get_updates  # type: ignore[attr-defined]
    service.run_forever()

    assert repository.heartbeats == 1
    assert repository.checkpoint == 43


@dataclass
class FakeFetcher:
    fail: bool = False

    def fetch(self, _source: object) -> FetchedContent:
        if self.fail:
            raise SourceFetchError("temporary", retryable=True)
        return FetchedContent(
            final_url="https://writer.substack.com/p/good",
            content_type="text/html",
            body=b"html",
        )


class FakeExtractor:
    def extract(self, _fetched: FetchedContent) -> ExtractedArticle:
        return ExtractedArticle(
            title="A Production Article",
            markdown="# Heading\n\n" + ("Durable knowledge sentence. " * 30),
            authors=("Ada",),
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            canonical_url="https://writer.substack.com/p/good",
        )


class UnusedImageFetcher:
    def fetch(self, _url: str) -> None:
        raise AssertionError("an article without images must not fetch image assets")


class WorkerImageFetcher:
    def fetch(self, url: str) -> FetchedImage:
        content = b"worker-image"
        return FetchedImage(
            original_url=url,
            final_url=url,
            content_type="image/png",
            extension="png",
            content_fingerprint=f"sha256:{hashlib.sha256(content).hexdigest()}",
            body=content,
            width=1,
            height=1,
        )


class FakeImageExtractor:
    def extract(self, _fetched: FetchedContent) -> ExtractedArticle:
        return ExtractedArticle(
            title="An Illustrated Article",
            markdown=("Knowledge before. " * 20) + "\n\n![Diagram](ka-image://0000)",
            authors=(),
            published_at=None,
            canonical_url="https://writer.substack.com/p/good",
            images=(
                ExtractedImage(
                    placeholder="ka-image://0000",
                    original_url="https://cdn.example/diagram.png",
                    alt_text="Diagram",
                ),
            ),
        )


class FakeEmbeddings:
    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=tuple((0.1, 0.2, 0.3) for _ in texts),
            model="text-embedding-test",
            dimensions=3,
            input_tokens=12,
        )


class FakeWorkerRepository:
    def __init__(self) -> None:
        self.transitions: list[tuple[IngestionState, IngestionState]] = []
        self.ready = False
        self.retry: bool | None = None
        self.registered = False
        self.stored = False
        self.heartbeats = 0
        self.claim_calls = 0
        self.notifications: tuple[PendingNotification, ...] = ()
        self.delivered: list[uuid.UUID] = []
        self.deferred: list[uuid.UUID] = []
        self.terminal_state = IngestionState.RETRY_SCHEDULED

    def transition(
        self,
        _job_id: uuid.UUID,
        *,
        expected: IngestionState,
        target: IngestionState,
    ) -> None:
        self.transitions.append((expected, target))

    def find_document_id(self, _key: str) -> None:
        return None

    def register_document(self, **_kwargs: object) -> None:
        self.registered = True

    def ensure_projection_generation(self, **_kwargs: object) -> uuid.UUID:
        return uuid.uuid4()

    def store_chunks(self, **_kwargs: object) -> None:
        self.stored = True

    def mark_ready(self, _job_id: uuid.UUID, *, document: object) -> None:
        del document
        self.ready = True

    def claim_notifications(self, **_kwargs: object) -> tuple[PendingNotification, ...]:
        return self.notifications

    def fail_or_retry(
        self,
        _job: ClaimedJob,
        *,
        error: Exception,
        retryable: bool,
    ) -> IngestionState:
        del error
        self.retry = retryable
        return self.terminal_state

    def heartbeat(self, **_kwargs: str) -> None:
        self.heartbeats += 1

    def claim(self, **_kwargs: str) -> None:
        self.claim_calls += 1
        return None

    def mark_notification_delivered(self, notification_id: uuid.UUID) -> None:
        self.delivered.append(notification_id)

    def defer_notification(self, notification_id: uuid.UUID) -> None:
        self.deferred.append(notification_id)

    def subscribers(self, _job_id: uuid.UUID) -> tuple[JobSubscriber, ...]:
        return (JobSubscriber(recipient_key="7", request_message_id="5"),)


def claimed_job() -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid.uuid4(),
        source_url="https://writer.substack.com/p/good",
        normalized_source_key="substack:key",
        source_type="article",
        source_provider="substack",
        attempt_count=1,
    )


def make_worker(
    tmp_path: Path,
    repository: FakeWorkerRepository,
    *,
    fetcher: FakeFetcher | None = None,
    extractor: object | None = None,
    image_fetcher: object | None = None,
    telegram: object | None = None,
) -> IngestionWorker:
    return IngestionWorker(
        repository=cast(Any, repository),
        classifier=SourceClassifier(),
        fetcher=cast(Any, fetcher or FakeFetcher()),
        extractors={
            SourceProvider.MEDIUM: cast(Any, extractor or FakeExtractor()),
            SourceProvider.SUBSTACK: cast(Any, extractor or FakeExtractor()),
            SourceProvider.X: cast(Any, extractor or FakeExtractor()),
        },
        asset_materializer=ArticleAssetMaterializer(
            cast(Any, image_fetcher or UnusedImageFetcher())
        ),
        vault=FileSystemVaultRepository(tmp_path / "vault"),
        chunker=MarkdownChunker(),
        embeddings=FakeEmbeddings(),
        telegram=cast(Any, telegram),
        poll_seconds=0.01,
    )


def test_worker_runs_complete_ingestion_pipeline(tmp_path: Path) -> None:
    repository = FakeWorkerRepository()
    worker = make_worker(tmp_path, repository)

    worker.process_job(claimed_job())

    assert repository.registered
    assert repository.stored
    assert repository.ready
    assert (IngestionState.COMMITTING, IngestionState.INDEXING) in repository.transitions
    assert next((tmp_path / "vault").rglob("*.md")).is_file()


def test_worker_persists_article_image_bundle(tmp_path: Path) -> None:
    repository = FakeWorkerRepository()
    worker = make_worker(
        tmp_path,
        repository,
        extractor=FakeImageExtractor(),
        image_fetcher=WorkerImageFetcher(),
    )

    worker.process_job(claimed_job())

    markdown_path = next((tmp_path / "vault" / "Articles").rglob("*.md"))
    markdown = markdown_path.read_text()
    asset_path = next((tmp_path / "vault" / "Assets").rglob("*.png"))
    assert "![[Assets/" in markdown
    assert asset_path.read_bytes() == b"worker-image"
    assert repository.ready


def test_worker_schedules_retry_for_temporary_fetch_failure(tmp_path: Path) -> None:
    repository = FakeWorkerRepository()
    worker = make_worker(tmp_path, repository, fetcher=FakeFetcher(fail=True))

    worker.process_job(claimed_job())

    assert repository.retry is True


def test_worker_loop_heartbeats_and_stops(tmp_path: Path) -> None:
    repository = FakeWorkerRepository()
    worker = make_worker(tmp_path, repository)

    def claim_and_stop(**_kwargs: str) -> None:
        worker.stop()
        return None

    repository.claim = claim_and_stop  # type: ignore[method-assign]
    worker.run_forever()

    assert repository.heartbeats == 1


def test_worker_runs_and_dispatches_without_telegram_client(tmp_path: Path) -> None:
    repository = FakeWorkerRepository()
    worker = make_worker(tmp_path, repository)

    # Notification-free: the full pipeline (fetch, extract, vault commit,
    # chunk, embed, index, ready) must complete with no Telegram client, and
    # dispatch must be a safe no-op that never claims or fabricates recipients.
    worker.process_job(claimed_job())
    worker.dispatch_notifications()

    assert repository.registered
    assert repository.stored
    assert repository.ready
    assert repository.delivered == []
    assert repository.deferred == []
    assert next((tmp_path / "vault").rglob("*.md")).is_file()


def test_worker_delivers_and_defers_notifications(tmp_path: Path) -> None:
    repository = FakeWorkerRepository()
    notification_id = uuid.uuid4()
    repository.notifications = (PendingNotification(notification_id, "7", "5", "Saved"),)
    telegram = FakeTelegram()
    worker = make_worker(tmp_path, repository)
    worker._telegram = cast(Any, telegram)

    worker.dispatch_notifications()

    assert repository.delivered == [notification_id]
    repository.delivered.clear()
    worker._telegram = cast(Any, FailingTelegram())
    worker.dispatch_notifications()
    assert repository.deferred == [notification_id]


def test_worker_notifies_on_terminal_failure(tmp_path: Path) -> None:
    repository = FakeWorkerRepository()
    repository.terminal_state = IngestionState.FAILED
    telegram = FakeTelegram()
    worker = make_worker(tmp_path, repository, fetcher=FakeFetcher(fail=True))
    worker._telegram = cast(Any, telegram)

    worker.process_job(claimed_job())

    assert "couldn't save" in telegram.sent[0][1]


def test_worker_failure_notification_delivery_error_is_logged_not_fatal(
    tmp_path: Path,
) -> None:
    repository = FakeWorkerRepository()
    repository.terminal_state = IngestionState.FAILED
    worker = make_worker(tmp_path, repository, fetcher=FakeFetcher(fail=True))
    worker._telegram = cast(Any, FailingTelegram())

    worker.process_job(claimed_job())

    # Delivery failure must not raise: the job outcome is already terminal.
    assert repository.retry is not None


def test_worker_failure_without_telegram_client_is_a_noop(tmp_path: Path) -> None:
    repository = FakeWorkerRepository()
    repository.terminal_state = IngestionState.FAILED
    worker = make_worker(tmp_path, repository, fetcher=FakeFetcher(fail=True))

    worker.process_job(claimed_job())

    assert repository.retry is not None


def test_worker_is_retryable_classification() -> None:
    import httpx

    from knowledge_assistant.domain.errors import DocumentConflictError

    assert IngestionWorker._is_retryable(httpx.NetworkError("temporary"))
    assert not IngestionWorker._is_retryable(ValueError("programming error"))
    assert not IngestionWorker._is_retryable(DocumentConflictError("already exists"))
