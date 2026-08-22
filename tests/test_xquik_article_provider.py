from __future__ import annotations

import httpx
import pytest

from knowledge_assistant.domain.sources import SourceFetchError
from knowledge_assistant.infrastructure.http.xquik_article_provider import (
    XquikArticleProvider,
)


def response(request: httpx.Request, payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=request)


def test_xquik_fetches_and_preserves_ordered_blocks_without_exposing_key() -> None:
    secret = "xq_super-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/x/articles/1234567890123456789"
        assert request.headers["x-api-key"] == secret
        assert secret not in str(request.url)
        return response(
            request,
            {
                "article": {
                    "title": "Ordered systems",
                    "coverImageUrl": "https://pbs.twimg.com/media/cover.jpg",
                    "createdAt": "Tue Mar 17 13:03:00 +0000 2026",
                    "contents": [
                        {"type": "header-one", "text": "Architecture"},
                        {
                            "type": "markdown",
                            "text": "```python\nprint('first')\n```",
                        },
                        {
                            "type": "markdown",
                            "text": (
                                "| Check | Result |\n"
                                "| --- | :--: |\n"
                                "| README exists | ✅ |\n"
                            ),
                        },
                        {
                            "type": "media",
                            "url": "https://pbs.twimg.com/media/diagram.png",
                            "previewUrl": "https://pbs.twimg.com/media/diagram.png",
                            "width": 2048,
                            "height": 1536,
                        },
                        {
                            "type": "paragraph",
                            "text": "Then the explanation follows.",
                            "inlineStyleRanges": [{"offset": 9, "length": 11, "style": "Bold"}],
                        },
                        {"type": "media"},
                        {"type": "divider"},
                    ],
                },
                "author": {"username": "example", "name": "Example Author"},
            },
        )

    provider = XquikArticleProvider(
        api_key=secret,
        client=httpx.Client(
            base_url="https://xquik.com/api/v1",
            headers={"x-api-key": secret},
            transport=httpx.MockTransport(handler),
        ),
    )

    article = provider.fetch_article("1234567890123456789")

    assert tuple(block.kind for block in article.blocks) == (
        "header-one",
        "code-block",
        "table",
        "media",
        "paragraph",
        "media",
        "divider",
    )
    assert article.blocks[1].text == "print('first')"
    assert article.blocks[1].code_language == "python"
    assert article.blocks[2].table_rows == (("Check", "Result"), ("README exists", "✅"))
    assert article.blocks[2].table_alignments == (None, "center")
    assert article.blocks[3].url == "https://pbs.twimg.com/media/diagram.png"
    assert article.blocks[3].width == 2048
    assert article.blocks[3].height == 1536
    assert article.blocks[4].inline_styles[0].style == "BOLD"
    assert article.blocks[5].url is None
    assert article.author_name == "Example Author"
    assert article.author_username == "example"
    assert secret not in repr(article)


def test_xquik_accepts_empty_paragraph_as_blank_line() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response(
            request,
            {
                "article": {
                    "title": "Blank lines",
                    "contents": [
                        {"type": "paragraph", "text": "before"},
                        {"type": "paragraph", "text": ""},
                        {"type": "paragraph", "text": "after"},
                    ],
                },
                "author": {"username": "example", "name": "Example Author"},
            },
        )

    provider = XquikArticleProvider(
        api_key="xq_key",
        client=httpx.Client(
            base_url="https://xquik.com/api/v1",
            transport=httpx.MockTransport(handler),
        ),
    )

    document = provider.fetch_article("1234567890123456789")

    assert [block.text for block in document.blocks] == ["before", "", "after"]


@pytest.mark.parametrize(
    "block",
    [
        {"type": "mystery-widget", "text": "cannot render"},
        {"type": "media", "previewUrl": "https://pbs.twimg.com/media/preview.jpg"},
        {
            "type": "media",
            "url": "https://pbs.twimg.com/media/preview.jpg",
            "width": 640,
        },
        {
            "type": "media",
            "url": "https://pbs.twimg.com/media/preview.jpg",
            "width": 0,
            "height": 480,
        },
        {
            "type": "paragraph",
            "text": "styled",
            "inlineStyleRanges": [{"offset": 0, "length": 6, "style": "SECRET_STYLE"}],
        },
        {"type": "markdown", "text": "# An arbitrary Markdown heading"},
        {
            "type": "markdown",
            "text": "```python\nprint('first')\n```\ntrailing prose",
        },
        {"type": "markdown", "text": "```python title=demo\nprint('first')\n```"},
        {"type": "markdown", "text": "```python\n\n```"},
    ],
)
def test_xquik_rejects_unknown_or_lossy_blocks(block: dict[str, object]) -> None:
    client = httpx.Client(
        base_url="https://xquik.com/api/v1",
        transport=httpx.MockTransport(
            lambda request: response(
                request,
                {
                    "article": {
                        "title": "Unsafe",
                        "contents": [block],
                    }
                },
            )
        ),
    )

    with pytest.raises(SourceFetchError, match="cannot be saved losslessly") as caught:
        XquikArticleProvider(api_key="xq_hidden", client=client).fetch_article(
            "1234567890123456789"
        )

    assert not caught.value.retryable
    assert "xq_hidden" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "retryable", "message"),
    [
        (401, False, "API key"),
        (402, False, "subscription"),
        (404, False, "could not find"),
        (424, True, "temporarily"),
        (429, True, "rate limit"),
        (502, True, "temporarily"),
    ],
)
def test_xquik_classifies_api_failures(
    status: int,
    retryable: bool,
    message: str,
) -> None:
    client = httpx.Client(
        base_url="https://xquik.com/api/v1",
        transport=httpx.MockTransport(
            lambda request: response(request, {"error": "failure"}, status)
        ),
    )

    with pytest.raises(SourceFetchError, match=message) as caught:
        XquikArticleProvider(api_key="xq_hidden", client=client).fetch_article(
            "1234567890123456789"
        )

    assert caught.value.retryable is retryable
    assert caught.value.status_code == status
