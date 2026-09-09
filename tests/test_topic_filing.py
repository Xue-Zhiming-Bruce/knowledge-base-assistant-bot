"""Topic filing: vault helpers, classifier, and worker path decisions."""

from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock

import pytest

from knowledge_assistant.application.worker import IngestionWorker
from knowledge_assistant.domain.documents import DocumentId
from knowledge_assistant.domain.errors import (
    DocumentConflictError,
    DocumentNotFoundError,
)
from knowledge_assistant.infrastructure.openai.topic_classifier import (
    OpenAITopicClassifier,
    _TopicChoice,
)
from knowledge_assistant.infrastructure.vault.filesystem import FileSystemVaultRepository
from tests.factories import DOCUMENT_ID, knowledge_document


def _committed_document(vault: FileSystemVaultRepository, path: PurePosixPath) -> None:
    vault.commit(knowledge_document(), path)


class TestVaultTopics:
    def test_topic_names_unions_provider_folders_and_skips_underscore(self, tmp_path: Path) -> None:
        vault = FileSystemVaultRepository(tmp_path)
        _committed_document(vault, PurePosixPath("Articles", "medium", "AI", "a.md"))
        _committed_document(vault, PurePosixPath("Articles", "substack", "Crypto", "b.md"))
        _committed_document(vault, PurePosixPath("Articles", "x", "_Pending", "c.md"))

        assert vault.topic_names() == ("AI", "Crypto")

    def test_move_document_renames_and_rejects_existing_target(self, tmp_path: Path) -> None:
        vault = FileSystemVaultRepository(tmp_path)
        old = PurePosixPath("Articles", "x", "_Pending", "doc.md")
        new = PurePosixPath("Articles", "x", "AI", "doc.md")
        _committed_document(vault, old)

        vault.move_document(old, new)

        assert vault.read(new) is not None
        with pytest.raises(DocumentNotFoundError):
            vault.read(old)
        with pytest.raises(DocumentConflictError):
            vault.move_document(old, new)


class TestTopicClassifier:
    def _classifier(self, parsed: _TopicChoice) -> OpenAITopicClassifier:
        client = MagicMock()
        client.responses.parse.return_value = MagicMock(output_parsed=parsed)
        return OpenAITopicClassifier(api_key="k", model="m", client=client)

    def test_returns_none_when_no_candidates(self) -> None:
        assert self._classifier(_TopicChoice(topic="AI", reason="r")).classify(
            title="t", content_excerpt="x", topics=()
        ) is None

    def test_matches_case_insensitively_and_rejects_unknown(self) -> None:
        classifier = self._classifier(_TopicChoice(topic="ai", reason="r"))
        assert classifier.classify(title="t", content_excerpt="x", topics=("AI",)) == "AI"
        unknown = self._classifier(_TopicChoice(topic="Cooking", reason="r"))
        assert unknown.classify(title="t", content_excerpt="x", topics=("AI",)) is None

    def test_none_choice_stays_none(self) -> None:
        classifier = self._classifier(_TopicChoice(topic=None, reason="r"))
        assert classifier.classify(title="t", content_excerpt="x", topics=("AI",)) is None


class TestWorkerVaultPath:
    def test_topic_none_uses_pending_folder(self) -> None:
        path = IngestionWorker._vault_path(
            "x", "Hello, World!", DocumentId("doc_0123456789abcdef0123456789abcdef")
        )
        assert path == PurePosixPath("Articles", "x", "_Pending", "hello-world-89abcdef.md")

    def test_topic_is_inserted_between_provider_and_filename(self) -> None:
        path = IngestionWorker._vault_path(
            "x",
            "Hello, World!",
            DOCUMENT_ID,
            topic="AI",
        )
        assert path == PurePosixPath("Articles", "x", "AI", "hello-world-89abcdef.md")
