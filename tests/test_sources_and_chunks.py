from pathlib import Path

import pytest

from knowledge_assistant.domain.chunks import MarkdownChunker
from knowledge_assistant.domain.documents import DocumentId, SourceProvider, SourceType
from knowledge_assistant.domain.sources import SourceClassifier, UnsupportedSourceError
from tests.factories import knowledge_document


def test_source_classifier_canonicalizes_supported_urls() -> None:
    classifier = SourceClassifier()

    medium = classifier.classify(
        "http://Example.Medium.com/story/?utm_source=email&keep=yes#section"
    )
    substack = classifier.classify("https://writer.substack.com/p/story?ref=home")

    assert medium.canonical_url == "https://example.medium.com/story?keep=yes"
    assert medium.normalized_source_key.startswith("medium:sha256:")
    assert substack.canonical_url == "https://writer.substack.com/p/story"
    assert DocumentId.derive_from_source(medium.normalized_source_key) == (
        DocumentId.derive_from_source(medium.normalized_source_key)
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/example/status/1234567890123456789?s=20",
        "https://twitter.com/example/status/1234567890123456789",
        "https://mobile.x.com/i/web/status/1234567890123456789/photo/1",
    ],
)
def test_source_classifier_normalizes_x_post_aliases(url: str) -> None:
    source = SourceClassifier().classify(url)

    assert source.canonical_url == "https://x.com/i/status/1234567890123456789"
    assert source.normalized_source_key == "x:post:1234567890123456789"
    assert source.source_type is SourceType.SOCIAL_POST
    assert source.provider is SourceProvider.X


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "https://example.com/article",
        "https://user:pass@medium.com/a",
        "https://x.com/example",
    ],
)
def test_source_classifier_rejects_unsupported_or_unsafe_urls(url: str) -> None:
    with pytest.raises(UnsupportedSourceError):
        SourceClassifier().classify(url)


def test_markdown_chunker_retains_heading_paths_and_is_deterministic() -> None:
    document = knowledge_document(
        body="# Intro\n\n" + ("First paragraph. " * 50) + "\n\n## Detail\n\n" + ("Evidence. " * 60)
    )
    chunker = MarkdownChunker(max_characters=500)

    chunks = chunker.chunk(document)

    assert len(chunks) >= 2
    assert chunks[-1].heading_path == ("Intro", "Detail")
    assert chunks == chunker.chunk(document)


def test_chunker_validates_configuration_and_content(tmp_path: Path) -> None:
    del tmp_path
    with pytest.raises(ValueError, match="at least 500"):
        MarkdownChunker(max_characters=10)
