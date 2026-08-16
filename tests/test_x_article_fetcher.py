from __future__ import annotations

import pytest

from knowledge_assistant.domain.sources import (
    ClassifiedSource,
    SourceClassifier,
    SourceFetchError,
)
from knowledge_assistant.domain.x_articles import (
    XArticleBlock,
    XArticleDocument,
    XArticleInlineStyle,
)
from knowledge_assistant.infrastructure.extraction.article import XArticleExtractor
from knowledge_assistant.infrastructure.http.x_article_fetcher import XArticleFetcher


def x_source() -> ClassifiedSource:
    return SourceClassifier().classify("https://x.com/example/status/1234567890123456789")


class StaticArticleProvider:
    def __init__(self, article: XArticleDocument) -> None:
        self.article = article
        self.post_ids: list[str] = []

    def fetch_article(self, post_id: str) -> XArticleDocument:
        self.post_ids.append(post_id)
        return self.article


def test_xquik_only_fetcher_preserves_exact_rich_article_order_and_metadata() -> None:
    provider = StaticArticleProvider(
        XArticleDocument(
            title="Ordered Article",
            author_name="Ada Example",
            author_username="ada",
            created_at="Tue Mar 17 13:03:00 +0000 2026",
            blocks=(
                XArticleBlock(kind="header-one", text="Introduction"),
                XArticleBlock(
                    kind="code-block",
                    text="print('ordered')",
                    code_language="python",
                ),
                XArticleBlock(
                    kind="paragraph",
                    text=(
                        "The code is followed by its figure and substantial explanatory prose. " * 3
                    ),
                ),
                XArticleBlock(kind="paragraph", text="$$\nE = mc^2\n$$"),
                XArticleBlock(
                    kind="table",
                    table_rows=(("Check", "Result"), ("README exists", "✅")),
                    table_alignments=(None, "center"),
                ),
                XArticleBlock(kind="media"),
                XArticleBlock(
                    kind="media",
                    url="https://pbs.twimg.com/media/ordered.jpg",
                    alt_text="Ordered figure",
                    width=2048,
                    height=1536,
                ),
                XArticleBlock(
                    kind="paragraph",
                    text="Conclusion with additional durable context. " * 3,
                ),
            ),
        )
    )

    fetched = XArticleFetcher(article_provider=provider).fetch(x_source())
    article = XArticleExtractor().extract(fetched)

    assert provider.post_ids == ["1234567890123456789"]
    assert article.title == "Ordered Article"
    assert article.authors == ("Ada Example",)
    assert article.published_at is not None
    assert len(article.images) == 1
    assert article.images[0].original_url.endswith("/ordered.jpg")
    assert article.images[0].expected_width == 2048
    assert article.images[0].expected_height == 1536
    ordered_fragments = (
        "## Introduction",
        "```python",
        "print('ordered')",
        "The code is followed",
        "$$\nE = mc^2\n$$",
        "| Check",
        "| README exists",
        "[Media unavailable in Xquik response.]",
        "ka-image://0000",
        "Conclusion with additional",
    )
    positions = tuple(article.markdown.index(item) for item in ordered_fragments)
    assert positions == tuple(sorted(positions))


def test_xquik_fetcher_preserves_cover_and_all_ordered_inline_images() -> None:
    provider = StaticArticleProvider(
        XArticleDocument(
            title="Illustrated Article",
            cover_image_url="https://pbs.twimg.com/media/cover.jpg",
            blocks=(
                XArticleBlock(kind="paragraph", text="Opening context. " * 20),
                XArticleBlock(
                    kind="media",
                    url="https://pbs.twimg.com/media/first.png",
                    alt_text="First diagram",
                    width=1600,
                    height=900,
                ),
                XArticleBlock(kind="paragraph", text="Middle context. " * 20),
                XArticleBlock(
                    kind="media",
                    url="https://pbs.twimg.com/media/second.png",
                    alt_text="Second diagram",
                    width=1200,
                    height=800,
                ),
            ),
        )
    )

    article = XArticleExtractor().extract(
        XArticleFetcher(article_provider=provider).fetch(x_source())
    )

    assert tuple(image.original_url for image in article.images) == (
        "https://pbs.twimg.com/media/cover.jpg",
        "https://pbs.twimg.com/media/first.png",
        "https://pbs.twimg.com/media/second.png",
    )
    assert article.images[1].expected_width == 1600
    assert article.images[2].expected_height == 800
    placeholders = tuple(image.placeholder for image in article.images)
    positions = tuple(article.markdown.index(value) for value in placeholders)
    assert positions == tuple(sorted(positions))


def test_xquik_only_fetcher_applies_utf16_inline_styles() -> None:
    text = "😀 bold text followed by enough explanatory content. " * 5
    provider = StaticArticleProvider(
        XArticleDocument(
            title="Styled Article",
            blocks=(
                XArticleBlock(
                    kind="paragraph",
                    text=text,
                    inline_styles=(XArticleInlineStyle(offset=3, length=4, style="BOLD"),),
                ),
            ),
        )
    )

    article = XArticleExtractor().extract(
        XArticleFetcher(article_provider=provider).fetch(x_source())
    )

    assert "😀 **bold** text" in article.markdown


def test_xquik_only_fetcher_rejects_non_x_sources_and_short_or_unknown_content() -> None:
    provider = StaticArticleProvider(
        XArticleDocument(
            title="Unsafe",
            blocks=(XArticleBlock(kind="paragraph", text="short"),),
        )
    )
    fetcher = XArticleFetcher(article_provider=provider)

    with pytest.raises(SourceFetchError, match="non-X"):
        fetcher.fetch(SourceClassifier().classify("https://medium.com/example/article"))
    with pytest.raises(SourceFetchError, match="too short"):
        fetcher.fetch(x_source())

    provider.article = XArticleDocument(
        title="Unknown",
        blocks=(XArticleBlock(kind="vendor-widget", text="content " * 30),),
    )
    with pytest.raises(SourceFetchError, match="unsupported block type"):
        fetcher.fetch(x_source())
