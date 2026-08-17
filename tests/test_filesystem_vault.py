import hashlib
from pathlib import Path, PurePosixPath

import pytest

from knowledge_assistant.domain.documents import DocumentAsset
from knowledge_assistant.domain.errors import (
    DocumentConflictError,
    DocumentNotFoundError,
    InvariantViolationError,
    UnsafeVaultPathError,
)
from knowledge_assistant.infrastructure.vault.filesystem import FileSystemVaultRepository
from knowledge_assistant.ports.vault import VaultAsset
from tests.factories import DOCUMENT_ID, knowledge_document


def test_commit_read_and_find_document(tmp_path: Path) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")
    vault_path = PurePosixPath("Articles/durable-document.md")
    document = knowledge_document()

    committed = repository.commit(document, vault_path)
    loaded = repository.read(vault_path)
    found = repository.find_by_document_id(DOCUMENT_ID)

    assert loaded == committed
    assert found == committed
    assert (tmp_path / "vault" / "Articles" / "durable-document.md").is_file()


def test_identical_commit_is_idempotent(tmp_path: Path) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")
    vault_path = PurePosixPath("Articles/durable-document.md")
    document = knowledge_document()

    first = repository.commit(document, vault_path)
    second = repository.commit(document, vault_path)

    assert second.file_fingerprint == first.file_fingerprint


def test_changed_file_requires_expected_fingerprint(tmp_path: Path) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")
    vault_path = PurePosixPath("Articles/durable-document.md")
    first = repository.commit(knowledge_document(), vault_path)
    changed = knowledge_document(body="A deliberately changed revision.")

    with pytest.raises(DocumentConflictError, match="different content"):
        repository.commit(changed, vault_path)

    updated = repository.commit(
        changed,
        vault_path,
        expected_file_fingerprint=first.file_fingerprint,
    )
    assert updated.document == changed


def test_external_edit_causes_conflict(tmp_path: Path) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")
    vault_path = PurePosixPath("Articles/durable-document.md")
    first = repository.commit(knowledge_document(), vault_path)
    target = tmp_path / "vault" / "Articles" / "durable-document.md"
    target.write_text(target.read_text() + "\nUser edit.\n")

    with pytest.raises(DocumentConflictError, match="changed"):
        repository.commit(
            knowledge_document(body="Source refresh."),
            vault_path,
            expected_file_fingerprint=first.file_fingerprint,
        )


def test_removed_file_causes_conflict_when_update_was_expected(tmp_path: Path) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")

    with pytest.raises(DocumentConflictError, match="removed"):
        repository.commit(
            knowledge_document(),
            PurePosixPath("removed.md"),
            expected_file_fingerprint=f"sha256:{'0' * 64}",
        )


def test_find_ignores_unmanaged_markdown(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "ordinary-note.md").write_text("# A user note\n")
    repository = FileSystemVaultRepository(vault)

    assert repository.find_by_document_id(DOCUMENT_ID) is None


def test_symbolic_link_target_is_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("do not overwrite")
    (vault / "linked.md").symlink_to(outside)
    repository = FileSystemVaultRepository(vault)

    with pytest.raises(UnsafeVaultPathError, match="symbolic link"):
        repository.commit(knowledge_document(), PurePosixPath("linked.md"))


@pytest.mark.parametrize(
    "vault_path",
    [
        PurePosixPath("/absolute.md"),
        PurePosixPath("../escape.md"),
        PurePosixPath("not-markdown.txt"),
    ],
)
def test_unsafe_path_is_rejected(tmp_path: Path, vault_path: PurePosixPath) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")

    with pytest.raises(UnsafeVaultPathError):
        repository.commit(knowledge_document(), vault_path)


def test_missing_document_has_domain_error(tmp_path: Path) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")

    with pytest.raises(DocumentNotFoundError):
        repository.read(PurePosixPath("missing.md"))


def test_commit_bundle_writes_asset_before_document_and_is_idempotent(
    tmp_path: Path,
) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")
    content = b"valid-test-image-content"
    fingerprint = f"sha256:{hashlib.sha256(content).hexdigest()}"
    asset = VaultAsset(
        vault_path=PurePosixPath(
            f"Assets/{DOCUMENT_ID.value}/{fingerprint.removeprefix('sha256:')}.png"
        ),
        content=content,
        content_fingerprint=fingerprint,
    )
    metadata = DocumentAsset(
        original_url="https://cdn.example/image.png",
        vault_path=asset.vault_path.as_posix(),
        content_type="image/png",
        content_fingerprint=fingerprint,
        byte_size=len(content),
        width=1,
        height=1,
    )
    vault_path = PurePosixPath("Articles/durable-document.md")

    document = knowledge_document(assets=(metadata,))
    first = repository.commit_bundle(document, vault_path, (asset,))
    second = repository.commit_bundle(document, vault_path, (asset,))
    third = repository.commit_bundle(document, vault_path, ())

    assert first == second == third
    assert (tmp_path / "vault" / asset.vault_path).read_bytes() == content


def test_delete_removes_unchanged_document_and_its_asset_directory(tmp_path: Path) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")
    content = b"deletable-image"
    fingerprint = f"sha256:{hashlib.sha256(content).hexdigest()}"
    asset_path = PurePosixPath(
        f"Assets/{DOCUMENT_ID.value}/{fingerprint.removeprefix('sha256:')}.png"
    )
    metadata = DocumentAsset(
        original_url="https://cdn.example/image.png",
        vault_path=asset_path.as_posix(),
        content_type="image/png",
        content_fingerprint=fingerprint,
        byte_size=len(content),
        width=1,
        height=1,
    )
    document_path = PurePosixPath("Articles/durable-document.md")
    stored = repository.commit_bundle(
        knowledge_document(assets=(metadata,)),
        document_path,
        (VaultAsset(asset_path, content, fingerprint),),
    )

    repository.delete(stored)

    assert not (tmp_path / "vault" / document_path).exists()
    assert not (tmp_path / "vault" / "Assets" / DOCUMENT_ID.value).exists()


def test_delete_rejects_document_changed_after_read(tmp_path: Path) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")
    document_path = PurePosixPath("Articles/durable-document.md")
    stored = repository.commit(knowledge_document(), document_path)
    absolute_path = tmp_path / "vault" / document_path
    absolute_path.write_text("externally changed")

    with pytest.raises(DocumentConflictError, match="changed since"):
        repository.delete(stored)

    assert absolute_path.read_text() == "externally changed"


def test_commit_bundle_rejects_asset_not_declared_by_document(tmp_path: Path) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")
    content = b"image"
    fingerprint = f"sha256:{hashlib.sha256(content).hexdigest()}"
    asset = VaultAsset(
        vault_path=PurePosixPath("Assets/doc_ffffffffffffffffffffffffffffffff/x.png"),
        content=content,
        content_fingerprint=fingerprint,
    )

    with pytest.raises(InvariantViolationError, match="metadata"):
        repository.commit_bundle(
            knowledge_document(),
            PurePosixPath("Articles/durable-document.md"),
            (asset,),
        )


def test_commit_bundle_detects_missing_or_corrupt_canonical_asset(tmp_path: Path) -> None:
    repository = FileSystemVaultRepository(tmp_path / "vault")
    content = b"canonical-image"
    fingerprint = f"sha256:{hashlib.sha256(content).hexdigest()}"
    asset_path = PurePosixPath(
        f"Assets/{DOCUMENT_ID.value}/{fingerprint.removeprefix('sha256:')}.png"
    )
    metadata = DocumentAsset(
        original_url="https://cdn.example/image.png",
        vault_path=asset_path.as_posix(),
        content_type="image/png",
        content_fingerprint=fingerprint,
        byte_size=len(content),
        width=1,
        height=1,
    )
    document = knowledge_document(assets=(metadata,))
    document_path = PurePosixPath("Articles/durable-document.md")

    with pytest.raises(InvariantViolationError, match="missing or corrupt"):
        repository.commit_bundle(document, document_path, ())

    repository.commit_bundle(
        document,
        document_path,
        (VaultAsset(asset_path, content, fingerprint),),
    )
    absolute_asset_path = tmp_path / "vault" / asset_path
    absolute_asset_path.write_bytes(b"corrupt")

    with pytest.raises(DocumentConflictError, match="different content"):
        repository.commit_bundle(
            document,
            document_path,
            (VaultAsset(asset_path, content, fingerprint),),
        )
