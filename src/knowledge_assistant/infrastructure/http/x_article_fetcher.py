"""Direct Xquik-backed acquisition for rich X Articles only."""

from __future__ import annotations

from html import escape
from itertools import pairwise
from urllib.parse import urlsplit

from knowledge_assistant.domain.documents import SourceProvider
from knowledge_assistant.domain.sources import (
    ClassifiedSource,
    FetchedContent,
    SourceFetchError,
)
from knowledge_assistant.domain.x_articles import XArticleBlock, XArticleInlineStyle
from knowledge_assistant.ports.sources import XArticleProvider


class XArticleFetcher:
    """Fetch one rich X Article directly from Xquik without calling the X API."""

    def __init__(self, *, article_provider: XArticleProvider) -> None:
        self._article_provider = article_provider

    def fetch(self, source: ClassifiedSource) -> FetchedContent:
        if source.provider is not SourceProvider.X:
            raise SourceFetchError(
                "The X Article adapter received a non-X source.",
                retryable=False,
            )
        post_id = urlsplit(source.canonical_url).path.rsplit("/", 1)[-1]
        article = self._article_provider.fetch_article(post_id)

        body_parts: list[str] = []
        if article.cover_image_url:
            body_parts.append(
                f'<figure><img src="{escape(article.cover_image_url, quote=True)}" alt=""></figure>'
            )
        body_parts.extend(self._render_block(block) for block in article.blocks)
        body = "".join(body_parts)
        if len(" ".join(self._strip_tags(body).split())) < 100:
            raise SourceFetchError(
                "Xquik returned a rich X Article whose content was missing or too short.",
                retryable=False,
            )
        return self._html_document(
            source=source,
            title=article.title,
            author_name=article.author_name,
            author_username=article.author_username,
            published_at=article.created_at,
            body=body,
        )

    @classmethod
    def _render_block(cls, block: XArticleBlock) -> str:
        if block.kind == "divider":
            return "<hr>"
        if block.kind == "media":
            if not block.url:
                return "<p><em>[Media unavailable in Xquik response.]</em></p>"
            dimensions = (
                f' width="{block.width}" height="{block.height}"'
                if block.width is not None and block.height is not None
                else ""
            )
            return (
                f'<figure><img src="{escape(block.url, quote=True)}" '
                f'alt="{escape(block.alt_text, quote=True)}"{dimensions}></figure>'
            )
        if block.kind == "table":
            if (
                not block.table_rows
                or len(block.table_alignments) != len(block.table_rows[0])
            ):
                raise SourceFetchError(
                    "Ordered X Article table block is missing rows or alignments.",
                    retryable=False,
                )
            header, *body_rows = block.table_rows
            if any(len(row) != len(header) for row in body_rows):
                raise SourceFetchError(
                    "Ordered X Article table block has inconsistent rows.",
                    retryable=False,
                )
            headings = "".join(
                cls._table_cell("th", cell, block.table_alignments[column])
                for column, cell in enumerate(header)
            )
            body = "".join(
                "<tr>"
                + "".join(
                    cls._table_cell("td", cell, block.table_alignments[column])
                    for column, cell in enumerate(row)
                )
                + "</tr>"
                for row in body_rows
            )
            return f"<table><thead><tr>{headings}</tr></thead><tbody>{body}</tbody></table>"
        if block.text is None:
            raise SourceFetchError(
                f"Ordered X Article {block.kind!r} block is missing text.",
                retryable=False,
            )
        if block.kind == "code-block":
            language_attribute = (
                f' data-code-language="{escape(block.code_language, quote=True)}"'
                if block.code_language
                else ""
            )
            return f"<pre{language_attribute}><code>{escape(block.text)}</code></pre>"

        rendered = cls._styled_text(block.text, block.inline_styles)
        if block.kind == "unordered-list-item":
            return f"<ul><li>{rendered}</li></ul>"
        if block.kind == "ordered-list-item":
            return f"<ol><li>{rendered}</li></ol>"
        tag = {
            "paragraph": "p",
            "header-one": "h2",
            "header-two": "h3",
            "header-three": "h4",
            "header-four": "h5",
            "header-five": "h6",
            "header-six": "h6",
            "blockquote": "blockquote",
        }.get(block.kind)
        if tag is None:
            raise SourceFetchError(
                f"Ordered X Article contains unsupported block type {block.kind!r}.",
                retryable=False,
            )
        return f"<{tag}>{rendered}</{tag}>"

    @staticmethod
    def _table_cell(tag: str, text: str, alignment: str | None) -> str:
        attribute = f' align="{alignment}"' if alignment else ""
        return f"<{tag}{attribute}>{escape(text)}</{tag}>"

    @classmethod
    def _styled_text(
        cls,
        text: str,
        styles: tuple[XArticleInlineStyle, ...],
    ) -> str:
        ranges = tuple(
            (
                cls._utf16_index(text, style.offset),
                cls._utf16_index(text, style.offset + style.length),
                style.style,
            )
            for style in styles
        )
        boundaries = sorted({0, len(text), *(value for item in ranges for value in item[:2])})
        output: list[str] = []
        for start, end in pairwise(boundaries):
            segment = escape(text[start:end])
            active = [item for item in ranges if item[0] <= start and item[1] >= end]
            for kind in ("CODE", "ITALIC", "BOLD", "UNDERLINE", "STRIKETHROUGH"):
                if not any(item[2] == kind for item in active):
                    continue
                if kind == "CODE":
                    segment = f"<code>{segment}</code>"
                elif kind == "ITALIC":
                    segment = f"<em>{segment}</em>"
                elif kind == "BOLD":
                    segment = f"<strong>{segment}</strong>"
                elif kind == "UNDERLINE":
                    segment = f"<u>{segment}</u>"
                else:
                    segment = f"<s>{segment}</s>"
            output.append(segment)
        return "".join(output).replace("\n", "<br>")

    @staticmethod
    def _utf16_index(text: str, code_units: int) -> int:
        consumed = 0
        for index, character in enumerate(text):
            if consumed >= code_units:
                return index
            consumed += 2 if ord(character) > 0xFFFF else 1
        return len(text)

    @staticmethod
    def _html_document(
        *,
        source: ClassifiedSource,
        title: str,
        author_name: str | None,
        author_username: str | None,
        published_at: str | None,
        body: str,
    ) -> FetchedContent:
        head = [
            f"<title>{escape(title)}</title>",
            f'<meta property="og:title" content="{escape(title, quote=True)}">',
            f'<link rel="canonical" href="{escape(source.canonical_url, quote=True)}">',
        ]
        if author_name and author_username:
            author_label = f"{author_name} (@{author_username})"
            head.append(f'<meta name="author" content="{escape(author_label, quote=True)}">')
        if published_at:
            head.append(
                '<meta property="article:published_time" '
                f'content="{escape(published_at, quote=True)}">'
            )
        html = (
            "<html><head>"
            + "".join(head)
            + "</head><body><article>"
            + body
            + "</article></body></html>"
        )
        return FetchedContent(
            final_url=source.canonical_url,
            content_type="text/html",
            body=html.encode(),
        )

    @staticmethod
    def _strip_tags(value: str) -> str:
        output: list[str] = []
        in_tag = False
        for character in value:
            if character == "<":
                in_tag = True
            elif character == ">":
                in_tag = False
            elif not in_tag:
                output.append(character)
        return "".join(output)
