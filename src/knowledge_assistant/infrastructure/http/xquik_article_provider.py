"""Strict Xquik adapter and shared parser for ordered X Article blocks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx

from knowledge_assistant.domain.sources import SourceFetchError
from knowledge_assistant.domain.x_articles import (
    XArticleBlock,
    XArticleDocument,
    XArticleInlineStyle,
)

_TEXT_BLOCK_TYPES = {
    "paragraph",
    "header-one",
    "header-two",
    "header-three",
    "header-four",
    "header-five",
    "header-six",
    "ordered-list-item",
    "unordered-list-item",
    "blockquote",
    "code-block",
}
_BLOCK_TYPES = _TEXT_BLOCK_TYPES | {"media", "divider"}
_INLINE_STYLES = {"BOLD", "ITALIC", "CODE", "UNDERLINE", "STRIKETHROUGH"}
_FENCED_CODE_BLOCK = re.compile(
    r"\A```(?P<language>[A-Za-z0-9_+.#-]*)[ \t]*\r?\n"
    r"(?P<code>.*?)\r?\n```[ \t]*\Z",
    re.DOTALL,
)
_TABLE_DELIMITER = re.compile(r"\A(?P<left>:)?-+(?P<right>:)?\Z")


class XquikArticlePayloadParser:
    """Validate Xquik's response without changing its block order."""

    def parse(self, payload: object) -> XArticleDocument:
        if not isinstance(payload, Mapping):
            raise self._schema_error("top-level response is not an object")
        article = payload.get("article")
        if not isinstance(article, Mapping):
            self._raise_payload_error(payload)
            raise self._schema_error("article is missing")
        title = self._required_text(article, "title")
        contents = article.get("contents")
        if not isinstance(contents, Sequence) or isinstance(contents, (str, bytes)):
            raise self._schema_error("article.contents is not an array")
        if not contents:
            raise self._schema_error("article.contents is empty")
        blocks = tuple(self._parse_block(value, index) for index, value in enumerate(contents))
        cover = self._optional_https_url(article.get("coverImageUrl"), "coverImageUrl")
        created_at = self._optional_text(article.get("createdAt"), "createdAt")
        author_name, author_username = self._parse_author(payload.get("author"))
        return XArticleDocument(
            title=title,
            blocks=blocks,
            cover_image_url=cover,
            created_at=created_at,
            author_name=author_name,
            author_username=author_username,
        )

    def _parse_author(self, value: object) -> tuple[str | None, str | None]:
        if value is None:
            return None, None
        if not isinstance(value, Mapping):
            raise self._schema_error("author is not an object")
        name = self._optional_text(value.get("name"), "author.name")
        username = self._optional_text(value.get("username"), "author.username")
        if (name is None) != (username is None):
            raise self._schema_error("author name and username must be returned together")
        return name, username

    def _parse_block(self, value: object, index: int) -> XArticleBlock:
        if not isinstance(value, Mapping):
            raise self._schema_error(f"contents[{index}] is not an object")
        kind = self._required_text(value, "type")
        if kind == "markdown":
            return self._parse_markdown_block(value, index)
        if kind not in _BLOCK_TYPES:
            raise self._schema_error(f"contents[{index}] has unsupported block type {kind!r}")
        if kind == "divider":
            if value.get("text") not in {None, ""} or value.get("url") not in {None, ""}:
                raise self._schema_error(f"contents[{index}] divider contains lossy fields")
            return XArticleBlock(kind=kind)
        if kind == "media":
            url = self._optional_https_url(value.get("url"), f"contents[{index}].url")
            alt_text = self._optional_text(value.get("altText"), "altText") or ""
            preview_url = value.get("previewUrl")
            if preview_url is not None:
                self._optional_https_url(preview_url, f"contents[{index}].previewUrl")
            width = self._optional_positive_int(
                value.get("width"),
                f"contents[{index}].width",
            )
            height = self._optional_positive_int(
                value.get("height"),
                f"contents[{index}].height",
            )
            if (width is None) != (height is None):
                raise self._schema_error(
                    f"contents[{index}] media dimensions must be returned together"
                )
            if value.get("inlineStyleRanges") not in (None, ()):
                raise self._schema_error(f"contents[{index}] media contains inline styles")
            if url is None:
                if preview_url is not None or alt_text or width is not None or height is not None:
                    raise self._schema_error(f"contents[{index}].url is missing")
                # Xquik can return a bare media block when X no longer makes the
                # underlying attachment available. Preserve that ordered block;
                # the renderer emits an explicit unavailable-media marker.
                return XArticleBlock(kind=kind)
            return XArticleBlock(
                kind=kind,
                url=url,
                alt_text=alt_text,
                width=width,
                height=height,
            )

        # X's editor emits author-inserted blank lines as empty paragraphs;
        # an empty string is their lossless representation.
        text = self._block_text(value, "text")
        styles_value = value.get("inlineStyleRanges", ())
        if not isinstance(styles_value, Sequence) or isinstance(styles_value, (str, bytes)):
            raise self._schema_error(f"contents[{index}].inlineStyleRanges is not an array")
        styles = tuple(
            self._parse_style(style, index, style_index, text)
            for style_index, style in enumerate(styles_value)
        )
        return XArticleBlock(kind=kind, text=text, inline_styles=styles)

    def _parse_markdown_block(
        self,
        value: Mapping[str, Any],
        index: int,
    ) -> XArticleBlock:
        """Accept only the losslessly representable Markdown variants Xquik emits."""

        text = self._block_text(value, "text")
        if value.get("inlineStyleRanges") not in (None, ()):
            raise self._schema_error(f"contents[{index}] markdown contains inline styles")
        match = _FENCED_CODE_BLOCK.fullmatch(text)
        if match is not None and match.group("code"):
            return XArticleBlock(
                kind="code-block",
                text=match.group("code"),
                code_language=match.group("language"),
            )

        table = self._parse_markdown_table(text)
        if table is not None:
            rows, alignments = table
            return XArticleBlock(
                kind="table",
                table_rows=rows,
                table_alignments=alignments,
            )

        raise self._schema_error(
            f"contents[{index}] markdown is not one complete code block or table"
        )

    @staticmethod
    def _parse_markdown_table(
        text: str,
    ) -> tuple[tuple[tuple[str, ...], ...], tuple[str | None, ...]] | None:
        lines = text.splitlines()
        if len(lines) < 3 or any(not line.strip() for line in lines):
            return None

        rows = tuple(XquikArticlePayloadParser._split_table_row(line) for line in lines)
        if any(row is None for row in rows):
            return None
        parsed_rows = tuple(row for row in rows if row is not None)
        column_count = len(parsed_rows[0])
        if column_count == 0 or any(len(row) != column_count for row in parsed_rows):
            return None

        alignments: list[str | None] = []
        for cell in parsed_rows[1]:
            match = _TABLE_DELIMITER.fullmatch(cell.replace(" ", ""))
            if match is None:
                return None
            left, right = match.group("left"), match.group("right")
            alignment = (
                "center"
                if left and right
                else "left"
                if left
                else "right"
                if right
                else None
            )
            alignments.append(alignment)
        return (parsed_rows[:1] + parsed_rows[2:], tuple(alignments))

    @staticmethod
    def _split_table_row(line: str) -> tuple[str, ...] | None:
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            return None
        cells: list[str] = []
        current: list[str] = []
        escaped = False
        for character in stripped[1:-1]:
            if escaped:
                if character != "|":
                    current.append("\\")
                current.append(character)
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "|":
                cells.append("".join(current).strip())
                current = []
            else:
                current.append(character)
        if escaped:
            current.append("\\")
        cells.append("".join(current).strip())
        return tuple(cells)

    def _parse_style(
        self,
        value: object,
        block_index: int,
        style_index: int,
        text: str,
    ) -> XArticleInlineStyle:
        location = f"contents[{block_index}].inlineStyleRanges[{style_index}]"
        if not isinstance(value, Mapping):
            raise self._schema_error(f"{location} is not an object")
        offset = value.get("offset")
        length = value.get("length")
        style = value.get("style")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or not isinstance(length, int)
            or isinstance(length, bool)
            or not isinstance(style, str)
        ):
            raise self._schema_error(f"{location} has invalid fields")
        utf16_length = len(text.encode("utf-16-le")) // 2
        if offset < 0 or length <= 0 or offset + length > utf16_length:
            raise self._schema_error(f"{location} is outside its text")
        normalized_style = style.upper()
        if normalized_style not in _INLINE_STYLES:
            raise self._schema_error(f"{location} has unsupported style {style!r}")
        return XArticleInlineStyle(
            offset=offset,
            length=length,
            style=normalized_style,
        )

    @staticmethod
    def _raise_payload_error(payload: Mapping[object, object]) -> None:
        error = payload.get("error")
        if error == "article_not_found":
            raise SourceFetchError(
                "Xquik could not find a rich X Article for this URL. Ordinary X posts, "
                "long posts, and threads are not supported.",
                retryable=False,
                status_code=404,
            )
        if error in {"x_api_unavailable", "dependency_failed"}:
            raise SourceFetchError(
                "Xquik Article service is temporarily unavailable.",
                retryable=True,
            )
        if error == "rate_limit_exceeded":
            raise SourceFetchError(
                "Xquik Article rate limit was exceeded.",
                retryable=True,
                status_code=429,
            )

    @staticmethod
    def _block_text(value: Mapping[str, Any], key: str) -> str:
        """Block text may be empty (blank line) but must be present and a string."""

        result = value.get(key)
        if not isinstance(result, str):
            raise XquikArticlePayloadParser._schema_error(f"{key} is missing")
        return result

    @staticmethod
    def _required_text(value: Mapping[str, Any], key: str) -> str:
        result = value.get(key)
        if not isinstance(result, str) or not result.strip():
            raise XquikArticlePayloadParser._schema_error(f"{key} is missing or empty")
        return result

    @staticmethod
    def _optional_text(value: object, key: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise XquikArticlePayloadParser._schema_error(f"{key} is not text")
        return value or None

    @staticmethod
    def _optional_positive_int(value: object, key: str) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise XquikArticlePayloadParser._schema_error(f"{key} is not a positive integer")
        return value

    @staticmethod
    def _required_https_url(value: object, key: str) -> str:
        result = XquikArticlePayloadParser._optional_https_url(value, key)
        if result is None:
            raise XquikArticlePayloadParser._schema_error(f"{key} is missing")
        return result

    @staticmethod
    def _optional_https_url(value: object, key: str) -> str | None:
        result = XquikArticlePayloadParser._optional_text(value, key)
        if result is None:
            return None
        parsed = urlsplit(result)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise XquikArticlePayloadParser._schema_error(f"{key} is not a safe HTTPS URL")
        return result

    @staticmethod
    def _schema_error(detail: str) -> SourceFetchError:
        return SourceFetchError(
            f"Xquik Article response cannot be saved losslessly: {detail}.",
            retryable=False,
        )


class XquikArticleProvider:
    """Fetch Xquik Articles with a subscription or prepaid API key."""

    def __init__(
        self,
        *,
        api_key: str,
        max_response_bytes: int = 5_000_000,
        timeout_seconds: float = 20,
        client: httpx.Client | None = None,
        parser: XquikArticlePayloadParser | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        self._api_key = api_key
        self._max_response_bytes = max_response_bytes
        self._parser = parser or XquikArticlePayloadParser()
        self._client = client or httpx.Client(
            base_url="https://xquik.com/api/v1",
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "KnowledgeAssistant/0.1 (+personal knowledge ingestion)",
            },
        )

    def fetch_article(self, post_id: str) -> XArticleDocument:
        """Return one fully validated Article without reordering its blocks."""

        try:
            response = self._client.get(
                f"/x/articles/{post_id}",
                headers={"x-api-key": self._api_key},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise SourceFetchError(
                "Xquik Article request failed because of a temporary network problem.",
                retryable=True,
            ) from error
        self._raise_for_status(response)
        if len(response.content) > self._max_response_bytes:
            raise SourceFetchError(
                "Xquik Article response exceeds the configured size limit.",
                retryable=False,
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise SourceFetchError(
                "Xquik Article response was not valid JSON.",
                retryable=False,
            ) from error
        return self._parser.parse(payload)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 401:
            message = "Xquik rejected the configured API key."
        elif status == 402:
            message = "Xquik requires an active subscription or additional credits."
        elif status == 404:
            message = (
                "Xquik could not find a rich X Article for this URL. Ordinary X posts, "
                "long posts, and threads are not supported."
            )
        elif status == 429:
            message = "Xquik Article rate limit was exceeded."
        elif status in {424, 502} or status >= 500:
            message = f"Xquik Article service temporarily returned HTTP {status}."
        else:
            message = f"Xquik Article request returned HTTP {status}."
        raise SourceFetchError(
            message,
            retryable=status == 429 or status in {424, 502} or status >= 500,
            status_code=status,
        )
