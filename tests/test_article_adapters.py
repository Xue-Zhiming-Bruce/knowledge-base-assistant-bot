from datetime import UTC

import httpx
import pytest

from knowledge_assistant.domain.sources import (
    ExtractionError,
    FetchedContent,
    SourceClassifier,
    SourceFetchError,
)
from knowledge_assistant.infrastructure.extraction.article import ArticleExtractor
from knowledge_assistant.infrastructure.http.medium_feed_fallback import (
    MediumFeedFallbackFetcher,
)
from knowledge_assistant.infrastructure.http.safe_fetcher import SafeHttpFetcher


def test_article_extractor_returns_markdown_and_metadata() -> None:
    paragraphs = "".join(
        f"<p>This is substantial article paragraph {index} with useful knowledge.</p>"
        for index in range(12)
    )
    fetched = FetchedContent(
        final_url="https://writer.substack.com/p/architecture",
        content_type="text/html",
        body=(
            "<html><head><title>Architecture Notes</title>"
            '<meta name="author" content="Ada Example">'
            '<meta property="article:published_time" content="2026-07-01">'
            f"</head><body><article>{paragraphs}</article></body></html>"
        ).encode(),
    )

    article = ArticleExtractor().extract(fetched)

    assert article.title == "Architecture Notes"
    assert "substantial article" in article.markdown
    assert article.authors == ("Ada Example",)
    assert article.published_at is not None
    assert article.published_at.tzinfo is UTC


def test_article_extractor_preserves_image_position_with_safe_placeholder() -> None:
    paragraphs = "".join(
        f"<p>Substantial content before or after image number {index}.</p>" for index in range(12)
    )
    fetched = FetchedContent(
        final_url="https://writer.substack.com/p/images",
        content_type="text/html",
        body=(
            "<html><head><title>Illustrated Notes</title></head><body><article>"
            '<header><img alt="Author avatar" src="https://cdn.example/avatar.png"></header>'
            "<h1>Illustrated Notes</h1>"
            f"{paragraphs[:300]}"
            '<figure><img alt="Useful diagram" src="/small.jpg" '
            'srcset="/small.jpg 320w, '
            'https://cdn.example/image/fetch/$s_!x!,f_auto,q_auto/large.png 1280w">'
            "</figure>"
            f"{paragraphs[300:]}</article></body></html>"
        ).encode(),
    )

    article = ArticleExtractor().extract(fetched)

    assert len(article.images) == 1
    assert article.images[0].original_url == (
        "https://cdn.example/image/fetch/$s_!x!,f_auto,q_auto/large.png"
    )
    assert article.images[0].alt_text == "Useful diagram"
    assert f"![Useful diagram]({article.images[0].placeholder})" in article.markdown
    assert "## Illustrated Notes" in article.markdown
    assert "\n# Illustrated Notes" not in article.markdown


def test_article_extractor_rejects_missing_content() -> None:
    fetched = FetchedContent(
        final_url="https://medium.com/a",
        content_type="text/html",
        body=b"<html><title>Tiny</title><body>short</body></html>",
    )
    with pytest.raises(ExtractionError):
        ArticleExtractor().extract(fetched)


def test_article_extractor_collapses_substack_cards_and_redirect_links() -> None:
    paragraphs = "".join(
        "<p>Substantial surrounding article content for reliable extraction.</p>" for _ in range(12)
    )
    fetched = FetchedContent(
        final_url="https://writer.substack.com/p/cards",
        content_type="text/html",
        body=(
            "<html><head><title>Article Cards</title></head><body><article>"
            f"{paragraphs}"
            '<p>See <a href="https://www.google.com/url?'
            "q=https%3A%2F%2Fexample.com%2Fdirect%3Fx%3D1&amp;sa=D"
            '">the direct source</a>.</p>'
            '<p>Resources: [<a href="https://books.example/manning">Manning</a>]</p>'
            '<a href="https://publication.example/p/linked-story">'
            '<div class="digestPostEmbed-random">'
            "<h4>A Related Story</h4>"
            '<a href="https://substack.com/profile/1">Example Author</a>'
            "<span>April 19, 2025</span>"
            '<a href="https://publication.example/p/linked-story">Read full story</a>'
            "</div></a>"
            "</article></body></html>"
        ).encode(),
    )

    article = ArticleExtractor().extract(fetched)

    assert "[A Related Story](https://publication.example/p/linked-story)" in article.markdown
    assert "Example Author" not in article.markdown
    assert "Read full story" not in article.markdown
    assert "google.com/url" not in article.markdown
    assert "[the direct source](https://example.com/direct?x=1)" in article.markdown
    assert "[Manning](https://books.example/manning)" in article.markdown
    assert "[[Manning]" not in article.markdown
    assert "[####" not in article.markdown


def test_safe_fetcher_fetches_html_and_follows_provider_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/new"}, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html>article</html>",
            request=request,
        )

    fetcher = SafeHttpFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    source = SourceClassifier().classify("https://writer.substack.com/old")

    result = fetcher.fetch(source)

    assert result.final_url.endswith("/new")
    assert result.body == b"<html>article</html>"


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(429, True), (503, True), (404, False)],
)
def test_safe_fetcher_classifies_http_failures(status: int, retryable: bool) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
    fetcher = SafeHttpFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=transport),
    )
    source = SourceClassifier().classify("https://medium.com/article")

    with pytest.raises(SourceFetchError) as caught:
        fetcher.fetch(source)

    assert caught.value.retryable is retryable


def test_safe_fetcher_rejects_private_resolution() -> None:
    fetcher = SafeHttpFetcher(resolver=lambda _host: ("127.0.0.1",))
    source = SourceClassifier().classify("https://medium.com/article")

    with pytest.raises(SourceFetchError, match="non-public"):
        fetcher.fetch(source)


def test_safe_fetcher_accepts_verified_substack_custom_domain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "substack.com":
            return httpx.Response(
                302,
                headers={"location": "https://publication.example/article"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=(
                b'<html data-publication_id="123">'
                b'<script src="https://substackcdn.com/app.js"></script></html>'
            ),
            request=request,
        )

    fetcher = SafeHttpFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = fetcher.fetch(SourceClassifier().classify("https://substack.com/@writer/note/p-123"))

    assert result.final_url == "https://publication.example/article"


def test_safe_fetcher_rejects_unverified_substack_custom_domain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "substack.com":
            return httpx.Response(
                302,
                headers={"location": "https://unrelated.example/article"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html>not a Substack publication</html>",
            request=request,
        )

    fetcher = SafeHttpFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(SourceFetchError, match="verified as a Substack"):
        fetcher.fetch(SourceClassifier().classify("https://substack.com/@writer/note/p-123"))


def test_medium_fetcher_uses_matching_feed_entry_after_direct_403() -> None:
    article_url = "https://medium.com/publication/target-story-a1b2c3d4e5f6"
    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss xmlns:content="http://purl.org/rss/1.0/modules/content/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
      <channel>
        <item>
          <title>Different story</title>
          <link>https://medium.com/publication/different-111111111111</link>
          <content:encoded><![CDATA[<p>Wrong article.</p>]]></content:encoded>
        </item>
        <item>
          <title>Target &amp; Story</title>
          <link>https://medium.com/publication/target-story-a1b2c3d4e5f6?source=rss</link>
          <guid>https://medium.com/p/a1b2c3d4e5f6</guid>
          <dc:creator>Example Author</dc:creator>
          <pubDate>Tue, 29 Jul 2026 12:30:00 GMT</pubDate>
          <content:encoded><![CDATA[
            <h1>Target Story</h1>
            <p>This is the complete requested Medium article body.</p>
          ]]></content:encoded>
        </item>
      </channel>
    </rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/feed/"):
            return httpx.Response(
                200,
                headers={"content-type": "text/xml; charset=utf-8"},
                content=feed,
                request=request,
            )
        return httpx.Response(403, request=request)

    safe_fetcher = SafeHttpFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = MediumFeedFallbackFetcher(safe_fetcher).fetch(SourceClassifier().classify(article_url))

    assert result.content_type == "text/html"
    assert b"<title>Target &amp; Story</title>" in result.body
    assert b"complete requested Medium article" in result.body
    assert b"Wrong article" not in result.body
    assert b'content="Example Author"' in result.body
    assert b"2026-07-29T12:30:00+00:00" in result.body


def test_medium_fetcher_does_not_fallback_for_non_403_failure() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, request=request))
    safe_fetcher = SafeHttpFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=transport),
    )

    with pytest.raises(SourceFetchError) as caught:
        MediumFeedFallbackFetcher(safe_fetcher).fetch(
            SourceClassifier().classify("https://medium.com/publication/story-a1b2c3d4e5f6")
        )

    assert caught.value.status_code == 404


def test_medium_fetcher_reports_article_missing_from_feed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/feed/"):
            return httpx.Response(
                200,
                headers={"content-type": "application/rss+xml"},
                content=(
                    b"<rss><channel><item><title>Other</title>"
                    b"<link>https://medium.com/publication/other-111111111111</link>"
                    b"</item></channel></rss>"
                ),
                request=request,
            )
        return httpx.Response(403, request=request)

    safe_fetcher = SafeHttpFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(SourceFetchError, match="not found in its public RSS feed"):
        MediumFeedFallbackFetcher(safe_fetcher).fetch(
            SourceClassifier().classify("https://medium.com/publication/story-a1b2c3d4e5f6")
        )


def test_medium_feed_fallback_derives_publication_feed_urls() -> None:
    assert (
        MediumFeedFallbackFetcher._feed_url("https://writer.medium.com/story-a1b2c3d4e5f6")
        == "https://writer.medium.com/feed"
    )
    assert MediumFeedFallbackFetcher._feed_url("https://medium.com/") is None
    assert (
        MediumFeedFallbackFetcher._feed_url("https://unsupported.example/story-a1b2c3d4e5f6")
        is None
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"<!DOCTYPE rss><rss/>", "unsafe RSS"),
        (b"<rss>", "malformed RSS"),
    ],
)
def test_medium_feed_fallback_rejects_unsafe_or_malformed_xml(
    body: bytes,
    message: str,
) -> None:
    source = SourceClassifier().classify("https://medium.com/publication/story-a1b2c3d4e5f6")
    feed = FetchedContent(
        final_url="https://medium.com/feed/publication",
        content_type="text/xml",
        body=body,
    )

    with pytest.raises(SourceFetchError, match=message):
        MediumFeedFallbackFetcher._article_from_feed(source, feed)


def test_medium_feed_fallback_rejects_incomplete_matching_entry() -> None:
    source = SourceClassifier().classify("https://medium.com/publication/story-a1b2c3d4e5f6")
    feed = FetchedContent(
        final_url="https://medium.com/feed/publication",
        content_type="text/xml",
        body=(
            b"<rss><channel><item><title>Story</title>"
            b"<guid>https://medium.com/p/a1b2c3d4e5f6</guid>"
            b"</item></channel></rss>"
        ),
    )

    with pytest.raises(SourceFetchError, match="complete article"):
        MediumFeedFallbackFetcher._article_from_feed(source, feed)
