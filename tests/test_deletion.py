from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath

from knowledge_assistant.application.deletion import ArticleDeletionService
from knowledge_assistant.domain.documents import DeletableArticle, DocumentId
from knowledge_assistant.infrastructure.vault.filesystem import FileSystemVaultRepository
from tests.factories import DOCUMENT_ID, knowledge_document


class FakeDeletionRegistry:
    def __init__(self, articles: tuple[DeletableArticle, ...]) -> None:
        self.articles = articles
        self.deleted: list[DocumentId] = []

    def find_articles_by_title(
        self,
        title: str,
        *,
        exact: bool,
        limit: int = 5,
    ) -> tuple[DeletableArticle, ...]:
        query = title.casefold()
        matches = tuple(
            item
            for item in self.articles
            if (item.title.casefold() == query if exact else query in item.title.casefold())
        )
        return matches[:limit]

    def delete_document(
        self,
        document_id: DocumentId,
        *,
        delete_from_vault: Callable[[], None],
    ) -> bool:
        if all(item.document_id != document_id for item in self.articles):
            return False
        delete_from_vault()
        self.deleted.append(document_id)
        self.articles = tuple(
            item for item in self.articles if item.document_id != document_id
        )
        return True


def article_reference(title: str, path: PurePosixPath) -> DeletableArticle:
    return DeletableArticle(
        document_id=DOCUMENT_ID,
        title=title,
        vault_path=path.as_posix(),
    )


def test_deletion_requires_exact_title_and_deletes_vault_and_registry(tmp_path: Path) -> None:
    path = PurePosixPath("Articles/a-durable-article.md")
    vault = FileSystemVaultRepository(tmp_path / "vault")
    vault.commit(knowledge_document(title="A Durable Article"), path)
    registry = FakeDeletionRegistry((article_reference("A Durable Article", path),))
    service = ArticleDeletionService(registry=registry, vault=vault)

    suggestion = service.delete_by_title("durable")
    deleted = service.delete_by_title("a durable article")

    assert not suggestion.deleted
    assert suggestion.suggestions == ("A Durable Article",)
    assert deleted.deleted_title == "A Durable Article"
    assert registry.deleted == [DOCUMENT_ID]
    assert not (tmp_path / "vault" / path).exists()
