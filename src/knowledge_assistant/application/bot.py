"""Thin Telegram ingestion client."""

from __future__ import annotations

import logging
import re
import threading
from uuid import uuid4

from knowledge_assistant.application.deletion import ArticleDeletionService
from knowledge_assistant.application.questions import QuestionService
from knowledge_assistant.domain.sources import SourceClassifier, UnsupportedSourceError
from knowledge_assistant.infrastructure.postgres.ingestion_repository import (
    PostgresIngestionRepository,
)
from knowledge_assistant.infrastructure.telegram.client import (
    TelegramApiError,
    TelegramClient,
    TelegramUpdate,
)

_URL_PATTERN = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)

_FEEDBACK_RESPONSES = {
    "recorded": "Thanks! Feedback recorded.",
    "duplicate": "Feedback for that answer was already recorded.",
    "no_turn": "No previous answer found to give feedback on.",
}


class TelegramPollingService:
    """Translate Telegram updates into idempotent Knowledge Engine submissions."""

    def __init__(
        self,
        *,
        telegram: TelegramClient,
        repository: PostgresIngestionRepository,
        classifier: SourceClassifier,
        allowed_user_ids: frozenset[int],
        poll_timeout_seconds: int,
        questions: QuestionService | None = None,
        deletions: ArticleDeletionService | None = None,
        instance_id: str | None = None,
    ) -> None:
        self._telegram = telegram
        self._repository = repository
        self._classifier = classifier
        self._allowed_user_ids = allowed_user_ids
        self._poll_timeout_seconds = poll_timeout_seconds
        self._questions = questions
        self._deletions = deletions
        self._instance_id = instance_id or f"bot-{uuid4()}"
        self._stop = threading.Event()
        self._logger = logging.getLogger(__name__)

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        offset = self._repository.get_checkpoint("update_offset")
        failure_delay = 1.0
        while not self._stop.is_set():
            self._repository.heartbeat(role="bot", instance_id=self._instance_id)
            if self._questions is not None:
                self._questions.cleanup_expired()
            try:
                updates = self._telegram.get_updates(
                    offset=offset,
                    timeout_seconds=self._poll_timeout_seconds,
                )
            except TelegramApiError:
                self._logger.exception("telegram_poll_failed")
                self._stop.wait(failure_delay)
                failure_delay = min(30.0, failure_delay * 2)
                continue
            failure_delay = 1.0
            for update in updates:
                self.process_update(update)
                offset = update.update_id + 1
                self._repository.set_checkpoint("update_offset", offset)

    def process_update(self, update: TelegramUpdate) -> None:
        message = update.message
        if message is None or message.chat_type != "private":
            return
        if message.sender_id not in self._allowed_user_ids:
            self._logger.warning(
                "telegram_update_rejected sender_id=%s update_id=%s",
                message.sender_id,
                update.update_id,
            )
            return
        principal_id = f"telegram:{message.sender_id}"
        command = message.text.strip().split(maxsplit=1)[0].lower().split("@", 1)[0]
        if command == "/answer":
            if self._questions is None:
                response = "Question Mode is not configured."
            else:
                created = self._questions.start(principal_id)
                response = (
                    "Question Mode started. Ask a question about your saved knowledge. "
                    "Use /end when finished."
                    if created
                    else "Question Mode is already active. Ask your next question."
                )
            self._telegram.send_message(
                chat_id=message.chat_id,
                text=response,
                reply_to_message_id=message.message_id,
            )
            return
        if command == "/end":
            ended = self._questions.end(principal_id) if self._questions is not None else False
            self._telegram.send_message(
                chat_id=message.chat_id,
                text=(
                    "Question Mode ended and temporary history was deleted."
                    if ended
                    else "Question Mode is not active."
                ),
                reply_to_message_id=message.message_id,
            )
            return
        if command == "/delete":
            parts = message.text.strip().split(maxsplit=1)
            title = parts[1].strip() if len(parts) == 2 else ""
            if not title:
                response = "Usage: /delete <exact article title>"
            elif self._deletions is None:
                response = "Article deletion is not configured."
            else:
                try:
                    deletion_result = self._deletions.delete_by_title(title)
                    if deletion_result.deleted:
                        response = f"Deleted: {deletion_result.deleted_title}"
                    elif deletion_result.suggestions:
                        choices = "\n".join(
                            f"- {item}" for item in deletion_result.suggestions
                        )
                        response = (
                            "No exact title match. Nothing was deleted. "
                            f"Did you mean:\n{choices}"
                        )
                    else:
                        response = "No article has that exact title. Nothing was deleted."
                except Exception:
                    self._logger.exception(
                        "article_deletion_failed sender_id=%s update_id=%s",
                        message.sender_id,
                        update.update_id,
                    )
                    response = "I couldn't delete that article safely. Nothing was deleted."
            self._telegram.send_message(
                chat_id=message.chat_id,
                text=response,
                reply_to_message_id=message.message_id,
            )
            return
        if command == "/feedback":
            parts = message.text.strip().split()
            direction = parts[1].lower() if len(parts) > 1 else ""
            if self._questions is None:
                response = "Question Mode is not configured."
            elif direction not in ("up", "down"):
                response = "Usage: /feedback up or /feedback down (optionally reply to the answer)."
            else:
                try:
                    feedback_result = self._questions.feedback(
                        principal_id=principal_id,
                        direction=direction,
                        reply_to_message_id=message.reply_to_message_id,
                    )
                    response = _FEEDBACK_RESPONSES.get(
                        feedback_result.status, "I couldn't record that feedback."
                    )
                except Exception:
                    self._logger.exception(
                        "feedback_failed sender_id=%s update_id=%s",
                        message.sender_id,
                        update.update_id,
                    )
                    response = "I couldn't record that feedback right now."
            self._telegram.send_message(
                chat_id=message.chat_id,
                text=response,
                reply_to_message_id=message.message_id,
            )
            return
        match = _URL_PATTERN.search(message.text)
        if (
            match is None
            and self._questions is not None
            and self._questions.is_active(principal_id)
        ):
            try:
                result = self._questions.answer(
                    principal_id=principal_id,
                    client_message_id=str(message.message_id),
                    question=message.text,
                )
                response = result.rendered_text
            except Exception:
                self._logger.exception(
                    "question_failed sender_id=%s update_id=%s",
                    message.sender_id,
                    update.update_id,
                )
                response = (
                    "I couldn't answer that question right now. "
                    "Question Mode is still active; please try again."
                )
            answer_message_id = self._telegram.send_message(
                chat_id=message.chat_id,
                text=response,
                reply_to_message_id=message.message_id,
            )
            if answer_message_id is not None:
                try:
                    self._questions.record_answer_message_id(
                        principal_id=principal_id,
                        client_message_id=str(message.message_id),
                        answer_message_id=str(answer_message_id),
                    )
                except Exception:
                    self._logger.exception(
                        "answer_message_link_failed sender_id=%s update_id=%s",
                        message.sender_id,
                        update.update_id,
                    )
            return
        if match is None:
            self._telegram.send_message(
                chat_id=message.chat_id,
                text=(
                    "Send a Medium, Substack, X Article, or blog article URL to save it, "
                    "use /answer to start Question Mode, or /delete <exact article title>."
                ),
                reply_to_message_id=message.message_id,
            )
            return
        try:
            source = self._classifier.classify(match.group(0).rstrip(".,;!?)"))
        except UnsupportedSourceError as error:
            self._telegram.send_message(
                chat_id=message.chat_id,
                text=str(error),
                reply_to_message_id=message.message_id,
            )
            return
        submission = self._repository.submit(
            idempotency_key=f"telegram:update:{update.update_id}",
            source=source,
            recipient_key=str(message.chat_id),
            request_message_id=str(message.message_id),
        )
        status = (
            "Got it - I'm saving and indexing that knowledge now."
            if submission.created
            else "That source is already being processed. I'll notify you when it is ready."
        )
        self._telegram.send_message(
            chat_id=message.chat_id,
            text=status,
            reply_to_message_id=message.message_id,
        )
