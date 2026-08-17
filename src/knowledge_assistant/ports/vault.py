"""Canonical vault persistence contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from knowledge_assistant.domain.documents import DocumentId, KnowledgeDocument


@dataclass(frozen=True, slots=True)
class VaultAsset:
    vault_path: PurePosixPath
    content: bytes
    content_fingerprint: str


@dataclass(frozen=True, slots=True)
class StoredKnowledgeDocument:
    document: KnowledgeDocument
    vault_path: PurePosixPath
    file_fingerprint: str


class VaultRepository(Protocol):
    def read(self, vault_path: PurePosixPath) -> StoredKnowledgeDocument:
        """Read and validate a canonical document."""

    def commit(
        self,
        document: KnowledgeDocument,
        vault_path: PurePosixPath,
        *,
        expected_file_fingerprint: str | None = None,
    ) -> StoredKnowledgeDocument:
        """Atomically commit a document, rejecting unexpected external changes."""

    def commit_bundle(
        self,
        document: KnowledgeDocument,
        vault_path: PurePosixPath,
        assets: tuple[VaultAsset, ...],
        *,
        expected_file_fingerprint: str | None = None,
    ) -> StoredKnowledgeDocument:
        """Commit immutable assets followed by the Markdown commit marker."""

    def find_by_document_id(self, document_id: DocumentId) -> StoredKnowledgeDocument | None:
        """Find a managed document by stable identity."""

    def delete(self, stored: StoredKnowledgeDocument) -> None:
        """Delete a previously read document and its managed assets if unchanged."""
