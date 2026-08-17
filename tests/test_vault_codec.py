import pytest

from knowledge_assistant.domain.documents import DocumentAsset
from knowledge_assistant.domain.errors import InvariantViolationError
from knowledge_assistant.infrastructure.vault.codec import KnowledgeDocumentCodec
from tests.factories import knowledge_document


def test_codec_round_trip_preserves_canonical_document() -> None:
    codec = KnowledgeDocumentCodec()
    document = knowledge_document()

    payload = codec.encode(document)
    decoded = codec.decode(payload)

    assert decoded == document
    assert payload.startswith(b"---\n")
    assert b"\n# A Durable Knowledge Document\n" in payload


def test_codec_round_trip_preserves_asset_metadata() -> None:
    asset = DocumentAsset(
        original_url="https://cdn.example/diagram.png",
        vault_path=(f"Assets/doc_0123456789abcdef0123456789abcdef/{'a' * 64}.png"),
        content_type="image/png",
        content_fingerprint=f"sha256:{'a' * 64}",
        byte_size=123,
        width=10,
        height=20,
        alt_text="Architecture diagram",
    )
    document = knowledge_document(assets=(asset,))

    decoded = KnowledgeDocumentCodec().decode(KnowledgeDocumentCodec().encode(document))

    assert decoded.revision.assets == (asset,)


def test_codec_accepts_schema_one_document_without_assets_key() -> None:
    codec = KnowledgeDocumentCodec()
    payload = codec.encode(knowledge_document()).replace(b"assets: []\n", b"")

    decoded = codec.decode(payload)

    assert decoded.revision.assets == ()


def test_codec_rejects_title_mismatch() -> None:
    codec = KnowledgeDocumentCodec()
    payload = codec.encode(knowledge_document()).replace(
        b"# A Durable Knowledge Document",
        b"# A Different Title",
    )

    with pytest.raises(InvariantViolationError, match="top-level title"):
        codec.decode(payload)


def test_codec_rejects_tampered_content() -> None:
    codec = KnowledgeDocumentCodec()
    document = knowledge_document()
    payload = codec.encode(document).replace(b"A supported fact.", b"An unsupported change.")

    with pytest.raises(InvariantViolationError, match="fingerprint"):
        codec.decode(payload)


def test_codec_rejects_malformed_frontmatter() -> None:
    codec = KnowledgeDocumentCodec()

    with pytest.raises(InvariantViolationError, match="frontmatter"):
        codec.decode(b"# No frontmatter\n")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"---\ntitle: test\n", "not closed"),
        (b"---\n- not\n- a\n- mapping\n---\n", "mapping"),
        (b"---\ntitle: [\n---\n", "invalid"),
        (b"\xff", "UTF-8"),
    ],
)
def test_codec_rejects_invalid_document_envelopes(
    payload: bytes,
    message: str,
) -> None:
    with pytest.raises(InvariantViolationError, match=message):
        KnowledgeDocumentCodec().decode(payload)
