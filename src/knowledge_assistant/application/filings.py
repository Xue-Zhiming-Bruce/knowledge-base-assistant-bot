"""Ask the user where to file articles the classifier could not place."""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath

from knowledge_assistant.infrastructure.postgres.ingestion_repository import (
    PendingTopicFiling,
    PostgresIngestionRepository,
)
from knowledge_assistant.infrastructure.telegram.client import (
    TelegramApiError,
    TelegramClient,
)
from knowledge_assistant.ports.vault import VaultRepository

_TOPIC_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}")


class TopicFilingService:
    """Notify pending filings over Telegram and resolve ``/topic`` replies."""

    def __init__(
        self,
        *,
        telegram: TelegramClient,
        repository: PostgresIngestionRepository,
        vault: VaultRepository,
    ) -> None:
        self._telegram = telegram
        self._repository = repository
        self._vault = vault
        self._logger = logging.getLogger(__name__)

    def notify_pending(self) -> None:
        topics = self._vault.topic_names()
        for filing in self._repository.unnotified_pending_filings():
            if filing.chat_id is None:
                # No Telegram subscriber (CLI submission): nothing to ask and
                # nobody to ask. Mark notified so it stops being re-fetched.
                self._repository.mark_filing_notified(
                    filing.document_id, chat_id=None, question_message_id=None
                )
                continue
            listing = ", ".join(topics) if topics else "(no topics yet)"
            text = (
                f'I couldn\'t decide where to file "{filing.title}".\n'
                f"Existing topics: {listing}\n"
                "Reply to this message with /topic <name>, "
                "or /topic new <Name> to create a new topic."
            )
            try:
                message_id = self._telegram.send_message(
                    chat_id=int(filing.chat_id),
                    text=text,
                )
            except (TelegramApiError, ValueError):
                self._logger.exception(
                    "filing_notification_failed document_id=%s",
                    filing.document_id,
                )
                continue
            self._repository.mark_filing_notified(
                filing.document_id,
                chat_id=filing.chat_id,
                question_message_id=str(message_id) if message_id is not None else None,
            )

    def resolve(self, filing: PendingTopicFiling, raw_topic: str) -> str:
        topic = " ".join(raw_topic.split())
        if _TOPIC_NAME_PATTERN.fullmatch(topic) is None:
            return "Topic name must use letters, digits, spaces, or dashes."
        old_path = PurePosixPath(filing.pending_vault_path)
        new_path = PurePosixPath(old_path.parts[0], old_path.parts[1], topic, old_path.parts[-1])
        try:
            resolved = self._repository.resolve_pending_filing(
                filing.document_id,
                new_vault_path=new_path.as_posix(),
                move=lambda: self._vault.move_document(old_path, new_path),
            )
        except Exception:
            self._logger.exception(
                "topic_filing_resolve_failed document_id=%s topic=%s",
                filing.document_id,
                topic,
            )
            return "I couldn't file that article. Nothing was changed."
        if not resolved:
            return "That filing is no longer open."
        return f'Filed "{filing.title}" under {topic}.'
