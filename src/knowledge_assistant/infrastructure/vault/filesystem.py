"""Atomic filesystem implementation of the canonical vault port."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath

from knowledge_assistant.domain.documents import DocumentId, KnowledgeDocument
from knowledge_assistant.domain.errors import (
    DocumentConflictError,
    DocumentNotFoundError,
    InvariantViolationError,
    UnsafeVaultPathError,
)
from knowledge_assistant.infrastructure.vault.codec import KnowledgeDocumentCodec
from knowledge_assistant.ports.vault import StoredKnowledgeDocument, VaultAsset

_ASSET_SUFFIXES = frozenset({".gif", ".jpg", ".png", ".webp"})


class FileSystemVaultRepository:
    """Store canonical Markdown beneath one explicitly managed vault root."""

    def __init__(
        self,
        root: Path,
        *,
        codec: KnowledgeDocumentCodec | None = None,
    ) -> None:
        self._root = root.expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._codec = codec or KnowledgeDocumentCodec()

    def read(self, vault_path: PurePosixPath) -> StoredKnowledgeDocument:
        target = self._safe_document_target(vault_path)
        try:
            payload = target.read_bytes()
        except FileNotFoundError as error:
            raise DocumentNotFoundError(str(vault_path)) from error
        document = self._codec.decode(payload)
        return StoredKnowledgeDocument(
            document=document,
            vault_path=vault_path,
            file_fingerprint=self._file_fingerprint(payload),
        )

    def commit(
        self,
        document: KnowledgeDocument,
        vault_path: PurePosixPath,
        *,
        expected_file_fingerprint: str | None = None,
    ) -> StoredKnowledgeDocument:
        return self.commit_bundle(
            document,
            vault_path,
            (),
            expected_file_fingerprint=expected_file_fingerprint,
        )

    def commit_bundle(
        self,
        document: KnowledgeDocument,
        vault_path: PurePosixPath,
        assets: tuple[VaultAsset, ...],
        *,
        expected_file_fingerprint: str | None = None,
    ) -> StoredKnowledgeDocument:
        target = self._safe_document_target(vault_path)
        payload = self._codec.encode(document)
        new_fingerprint = self._file_fingerprint(payload)
        current_payload = target.read_bytes() if target.exists() else None
        document_is_identical = False

        if current_payload is not None:
            current_fingerprint = self._file_fingerprint(current_payload)
            if current_fingerprint == new_fingerprint:
                document_is_identical = True
            elif expected_file_fingerprint is None:
                raise DocumentConflictError(f"{vault_path} already exists with different content")
            elif current_fingerprint != expected_file_fingerprint:
                raise DocumentConflictError(f"{vault_path} changed since it was last observed")
        elif expected_file_fingerprint is not None:
            raise DocumentConflictError(f"{vault_path} was removed since it was last observed")

        pending_assets: list[tuple[Path, VaultAsset]] = []
        metadata_by_path = {asset.vault_path: asset for asset in document.revision.assets}
        supplied_paths: set[str] = set()
        for asset in assets:
            path_key = asset.vault_path.as_posix()
            metadata = metadata_by_path.get(path_key)
            if metadata is None or metadata.content_fingerprint != asset.content_fingerprint:
                raise InvariantViolationError(
                    "supplied vault asset does not match document metadata"
                )
            if path_key in supplied_paths:
                raise InvariantViolationError("vault asset paths must be unique")
            supplied_paths.add(path_key)
            asset_target = self._safe_asset_target(
                asset.vault_path,
                document_id=document.revision.document_id,
            )
            if self._file_fingerprint(asset.content) != asset.content_fingerprint:
                raise InvariantViolationError("asset fingerprint does not match its content")
            if asset_target.exists():
                if self._file_fingerprint(asset_target.read_bytes()) != asset.content_fingerprint:
                    raise DocumentConflictError(f"{asset.vault_path} exists with different content")
            else:
                pending_assets.append((asset_target, asset))
        for path_key, metadata in metadata_by_path.items():
            if path_key in supplied_paths:
                continue
            asset_target = self._safe_asset_target(
                PurePosixPath(path_key),
                document_id=document.revision.document_id,
            )
            if (
                not asset_target.exists()
                or self._file_fingerprint(asset_target.read_bytes()) != metadata.content_fingerprint
            ):
                raise InvariantViolationError(f"canonical asset is missing or corrupt: {path_key}")

        # Assets are immutable and written before Markdown. The Markdown file is
        # therefore the bundle's commit marker; interrupted writes can only leave
        # harmless, content-addressed orphan assets.
        for asset_target, asset in pending_assets:
            self._atomic_write(asset_target, asset.content)
        if not document_is_identical:
            self._atomic_write(target, payload)

        return StoredKnowledgeDocument(
            document=document,
            vault_path=vault_path,
            file_fingerprint=new_fingerprint,
        )

    def find_by_document_id(self, document_id: DocumentId) -> StoredKnowledgeDocument | None:
        found: StoredKnowledgeDocument | None = None
        for path in self._root.rglob("*.md"):
            relative = PurePosixPath(path.relative_to(self._root).as_posix())
            try:
                candidate = self.read(relative)
            except (DocumentNotFoundError, InvariantViolationError):
                continue
            if candidate.document.revision.document_id != document_id:
                continue
            if found is not None:
                raise InvariantViolationError(
                    f"duplicate document_id found in vault: {document_id}"
                )
            found = candidate
        return found

    def delete(self, stored: StoredKnowledgeDocument) -> None:
        """Delete one unchanged canonical document and all of its managed assets."""

        target = self._safe_document_target(stored.vault_path)
        try:
            payload = target.read_bytes()
        except FileNotFoundError as error:
            raise DocumentNotFoundError(str(stored.vault_path)) from error
        if self._file_fingerprint(payload) != stored.file_fingerprint:
            raise DocumentConflictError(f"{stored.vault_path} changed since it was last observed")

        document_id = stored.document.revision.document_id
        asset_directory = self._root / "Assets" / document_id.value
        if asset_directory.exists():
            if asset_directory.is_symlink() or not asset_directory.is_dir():
                raise UnsafeVaultPathError("document asset directory is unsafe")
            self._reject_symlink_chain(asset_directory)
            assets = tuple(asset_directory.iterdir())
            if any(
                item.is_symlink()
                or not item.is_file()
                or item.suffix.lower() not in _ASSET_SUFFIXES
                for item in assets
            ):
                raise UnsafeVaultPathError("document asset directory contains unmanaged entries")
            for item in assets:
                item.unlink()
            asset_directory.rmdir()
            self._fsync_directory(asset_directory.parent)

        target.unlink()
        self._fsync_directory(target.parent)

    def _safe_document_target(self, vault_path: PurePosixPath) -> Path:
        if vault_path.is_absolute() or not vault_path.parts:
            raise UnsafeVaultPathError("vault path must be relative")
        if any(part in {"", ".", ".."} for part in vault_path.parts):
            raise UnsafeVaultPathError("vault path contains an unsafe segment")
        if vault_path.suffix.lower() != ".md":
            raise UnsafeVaultPathError("Knowledge Document path must end in .md")

        candidate = self._root.joinpath(*vault_path.parts)
        if candidate.is_symlink():
            raise UnsafeVaultPathError("Knowledge Document path must not be a symbolic link")
        self._reject_symlink_chain(candidate.parent)
        try:
            candidate.resolve(strict=False).relative_to(self._root)
        except ValueError as error:
            raise UnsafeVaultPathError("vault path escapes the configured root") from error
        return candidate

    def _safe_asset_target(
        self,
        vault_path: PurePosixPath,
        *,
        document_id: DocumentId,
    ) -> Path:
        if (
            vault_path.is_absolute()
            or len(vault_path.parts) != 3
            or vault_path.parts[:2] != ("Assets", document_id.value)
            or any(part in {"", ".", ".."} for part in vault_path.parts)
            or vault_path.suffix.lower() not in _ASSET_SUFFIXES
        ):
            raise UnsafeVaultPathError(
                "asset path must be Assets/<document_id>/<content-hash>.<image-extension>"
            )
        candidate = self._root.joinpath(*vault_path.parts)
        if candidate.is_symlink():
            raise UnsafeVaultPathError("asset path must not be a symbolic link")
        self._reject_symlink_chain(candidate.parent)
        try:
            candidate.resolve(strict=False).relative_to(self._root)
        except ValueError as error:
            raise UnsafeVaultPathError("asset path escapes the configured root") from error
        return candidate

    def _atomic_write(self, target: Path, payload: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_chain(target.parent)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, target)
            temporary_name = None
            self._fsync_directory(target.parent)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    def _reject_symlink_chain(self, directory: Path) -> None:
        current = directory
        while current != self._root:
            if current.is_symlink():
                raise UnsafeVaultPathError("vault paths must not traverse symbolic links")
            current = current.parent
            if self._root not in (current, *current.parents):
                raise UnsafeVaultPathError("vault path escapes the configured root")

    @staticmethod
    def _file_fingerprint(payload: bytes) -> str:
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
