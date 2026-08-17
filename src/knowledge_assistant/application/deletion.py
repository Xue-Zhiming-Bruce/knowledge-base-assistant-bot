"""Safe deletion of one canonical knowledge article by exact title."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from knowledge_assistant.domain.documents import DeletableArticle, DocumentId
from knowledge_assistant.domain.errors import InvariantViolationError
from knowledge_assistant.ports.vault import VaultRepository


class DeletionRegistry(Protocol):
    def find_articles_by_title(
        self,
        title: str,
        *,
        exact: bool,
        limit: int = 5,
    ) -> tuple[DeletableArticle, ...]: ...

    def delete_document(
        self,
        document_id: DocumentId,
        *,
        delete_from_vault: Callable[[], None],
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ArticleDeletionResult:
    deleted_title: str | None = None
    suggestions: tuple[str, ...] = ()

    @property
    def deleted(self) -> bool:
        return self.deleted_title is not None


class ArticleDeletionService:
    """Delete only an unambiguous, exact title match from registry and vault."""

    def __init__(self, *, registry: DeletionRegistry, vault: VaultRepository) -> None:
        self._registry = registry
        self._vault = vault

    def delete_by_title(self, title: str) -> ArticleDeletionResult:
        query = " ".join(title.split())
        if not query:
            return ArticleDeletionResult()

        matches = self._registry.find_articles_by_title(query, exact=True)
        if len(matches) != 1:
            suggestions = self._registry.find_articles_by_title(query, exact=False)
            return ArticleDeletionResult(
                suggestions=tuple(dict.fromkeys(item.title for item in suggestions))
            )

        match = matches[0]
        stored = self._vault.read(PurePosixPath(match.vault_path))
        if stored.document.revision.document_id != match.document_id:
            raise InvariantViolationError("vault document identity does not match the registry")

        # The registry calls the guarded vault operation inside its transaction:
        # a vault conflict rolls the database deletion back.
        if not self._registry.delete_document(
            match.document_id,
            delete_from_vault=lambda: self._vault.delete(stored),
        ):
            raise InvariantViolationError("document disappeared during deletion")
        return ArticleDeletionResult(deleted_title=match.title)
