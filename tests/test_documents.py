from dataclasses import replace
from datetime import datetime

import pytest

from knowledge_assistant.domain.documents import (
    DocumentAsset,
    DocumentId,
    IngestionProvenance,
    KnowledgeDocument,
    RevisionId,
    SourceProvider,
    SourceReference,
    SourceType,
)
from knowledge_assistant.domain.errors import InvariantViolationError
from tests.factories import knowledge_document


def valid_asset(*, document_id: str = "doc_0123456789abcdef0123456789abcdef") -> DocumentAsset:
    return DocumentAsset(
        original_url="https://cdn.example/image.png",
        vault_path=f"Assets/{document_id}/{'a' * 64}.png",
        content_type="image/png",
        content_fingerprint=f"sha256:{'a' * 64}",
        byte_size=100,
        width=10,
        height=10,
    )


def test_document_creation_is_normalized_and_deterministic() -> None:
    first = knowledge_document(body="Line with spaces.  \r\n\r\nSecond line.\r\n")
    second = knowledge_document(body="Line with spaces.\n\nSecond line.")

    assert first.markdown_body == "Line with spaces.\n\nSecond line.\n"
    assert first.revision.content_fingerprint == second.revision.content_fingerprint
    assert first.revision.revision_id == second.revision.revision_id


def test_changed_content_creates_new_revision() -> None:
    original = knowledge_document(body="Original.")
    changed = knowledge_document(body="Changed.")

    assert original.revision.document_id == changed.revision.document_id
    assert original.revision.revision_id != changed.revision.revision_id


def test_document_rejects_fingerprint_mismatch() -> None:
    document = knowledge_document()
    invalid_revision = replace(
        document.revision,
        content_fingerprint=f"sha256:{'0' * 64}",
    )

    with pytest.raises(InvariantViolationError, match="fingerprint"):
        KnowledgeDocument(revision=invalid_revision, markdown_body=document.markdown_body)


def test_document_id_has_stable_validated_shape() -> None:
    generated = DocumentId.new()

    assert generated.value.startswith("doc_")
    assert len(generated.value) == 36

    with pytest.raises(InvariantViolationError, match="document_id"):
        DocumentId("path-or-title-is-not-an-id")

    with pytest.raises(InvariantViolationError, match="revision_id"):
        RevisionId("not-a-revision")


def test_identifiers_have_string_representation() -> None:
    document = knowledge_document()

    assert str(document.revision.document_id) == document.revision.document_id.value
    assert str(document.revision.revision_id) == document.revision.revision_id.value


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:password@example.com/article",
        "/relative/article",
    ],
)
def test_source_reference_rejects_unsafe_or_non_http_urls(url: str) -> None:
    with pytest.raises(InvariantViolationError, match="source URL"):
        SourceReference(
            url=url,
            source_type=SourceType.ARTICLE,
            provider=SourceProvider.OTHER,
        )


def test_naive_source_datetime_is_rejected() -> None:
    document = knowledge_document()

    with pytest.raises(InvariantViolationError, match="timezone-aware"):
        replace(document.revision, published_at=datetime(2026, 7, 1))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("extractor", ""),
        ("extractor_version", "  "),
        ("normalizer_version", ""),
    ],
)
def test_ingestion_provenance_rejects_blank_fields(field: str, value: str) -> None:
    values = {
        "extractor": "medium",
        "extractor_version": "1",
        "normalizer_version": "1",
    }
    values[field] = value

    with pytest.raises(InvariantViolationError, match=field):
        IngestionProvenance(**values)


def test_blank_document_body_is_rejected() -> None:
    with pytest.raises(InvariantViolationError, match="blank"):
        knowledge_document(body=" \n ")


def test_document_asset_validates_network_path_format_and_dimensions() -> None:
    asset = valid_asset()
    with pytest.raises(InvariantViolationError, match="HTTPS"):
        replace(asset, original_url="http://cdn.example/image.png")
    with pytest.raises(InvariantViolationError, match="vault_path"):
        replace(asset, vault_path="../image.png")
    with pytest.raises(InvariantViolationError, match="content_type"):
        replace(asset, content_type="image/svg+xml")
    with pytest.raises(InvariantViolationError, match="fingerprint"):
        replace(asset, content_fingerprint="sha256:bad")
    with pytest.raises(InvariantViolationError, match="byte_size"):
        replace(asset, byte_size=0)
    with pytest.raises(InvariantViolationError, match="dimensions"):
        replace(asset, width=0)


def test_document_revision_rejects_foreign_and_duplicate_asset_paths() -> None:
    foreign = valid_asset(document_id="doc_ffffffffffffffffffffffffffffffff")
    with pytest.raises(InvariantViolationError, match="belong"):
        knowledge_document(assets=(foreign,))

    asset = valid_asset()
    with pytest.raises(InvariantViolationError, match="unique"):
        knowledge_document(assets=(asset, asset))
