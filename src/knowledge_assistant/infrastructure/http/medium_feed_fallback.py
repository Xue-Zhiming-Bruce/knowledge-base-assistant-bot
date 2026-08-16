"""Medium RSS fallback for direct requests blocked by an access challenge."""

from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from html import escape
from urllib.parse import urlsplit
from xml.etree import ElementTree

from knowledge_assistant.domain.documents import SourceProvider
from knowledge_assistant.domain.sources import (
    ClassifiedSource,
    FetchedContent,
    SourceFetchError,
)
from knowledge_assistant.infrastructure.http.safe_fetcher import SafeHttpFetcher

_CONTENT_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"
_CREATOR_TAG = "{http://purl.org/dc/elements/1.1/}creator"
_ARTICLE_ID = re.compile(r"(?:-|/)([0-9a-f]{8,32})/?$", re.IGNORECASE)


class MediumFeedFallbackFetcher:
    """Fetch Medium HTML directly, then use its public RSS feed on HTTP 403."""

    def __init__(self, safe_fetcher: SafeHttpFetcher) -> None:
        self._safe_fetcher = safe_fetcher

    def fetch(self, source: ClassifiedSource) -> FetchedContent:
        try:
            return self._safe_fetcher.fetch(source)
        except SourceFetchError as error:
            if source.provider is not SourceProvider.MEDIUM or error.status_code != 403:
                raise

        feed_url = self._feed_url(source.canonical_url)
        if feed_url is None:
            raise SourceFetchError(
                "Medium blocked direct access and no public feed could be derived.",
                retryable=False,
                status_code=403,
            )
        feed = self._safe_fetcher.fetch_related(
            source,
            feed_url,
            accepted_content_types={
                "application/rss+xml",
                "application/xml",
                "text/xml",
            },
        )
        return self._article_from_feed(source, feed)

    @staticmethod
    def _feed_url(article_url: str) -> str | None:
        parsed = urlsplit(article_url)
        hostname = (parsed.hostname or "").lower()
        segments = [segment for segment in parsed.path.split("/") if segment]
        if not hostname or not segments:
            return None
        if hostname == "medium.com":
            return f"https://medium.com/feed/{segments[0]}"
        if hostname.endswith(".medium.com"):
            return f"https://{hostname}/feed"
        return None

    @staticmethod
    def _article_from_feed(
        source: ClassifiedSource,
        feed: FetchedContent,
    ) -> FetchedContent:
        normalized_body = feed.body.upper()
        if b"<!DOCTYPE" in normalized_body or b"<!ENTITY" in normalized_body:
            raise SourceFetchError(
                "Medium returned an unsafe RSS document.",
                retryable=False,
            )
        try:
            root = ElementTree.fromstring(feed.body)
        except ElementTree.ParseError as error:
            raise SourceFetchError(
                "Medium returned malformed RSS.",
                retryable=False,
            ) from error

        requested_id = MediumFeedFallbackFetcher._article_id(source.canonical_url)
        requested_path = MediumFeedFallbackFetcher._normalized_path(source.canonical_url)
        for item in root.findall(".//item"):
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or "").strip()
            candidates = (link, guid)
            matches_id = requested_id is not None and any(
                MediumFeedFallbackFetcher._article_id(candidate) == requested_id
                for candidate in candidates
            )
            matches_path = any(
                MediumFeedFallbackFetcher._normalized_path(candidate) == requested_path
                for candidate in candidates
            )
            if not matches_id and not matches_path:
                continue
            return MediumFeedFallbackFetcher._to_html(
                item,
                final_url=link or source.canonical_url,
            )

        raise SourceFetchError(
            "Medium blocked direct access and the article was not found in its public RSS feed.",
            retryable=False,
            status_code=403,
        )

    @staticmethod
    def _to_html(item: ElementTree.Element, *, final_url: str) -> FetchedContent:
        title = (item.findtext("title") or "").strip()
        content = (item.findtext(_CONTENT_TAG) or "").strip()
        author = (item.findtext(_CREATOR_TAG) or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        if not title or not content:
            raise SourceFetchError(
                "Medium's RSS entry did not contain a complete article.",
                retryable=False,
            )
        published_iso = ""
        if published:
            try:
                published_iso = parsedate_to_datetime(published).isoformat()
            except (TypeError, ValueError):
                published_iso = ""
        head = [
            f"<title>{escape(title)}</title>",
            f'<link rel="canonical" href="{escape(final_url, quote=True)}">',
        ]
        if author:
            head.append(f'<meta name="author" content="{escape(author, quote=True)}">')
        if published_iso:
            head.append(
                '<meta property="article:published_time" '
                f'content="{escape(published_iso, quote=True)}">'
            )
        document = (
            "<html><head>"
            + "".join(head)
            + "</head><body><article>"
            + content
            + "</article></body></html>"
        )
        return FetchedContent(
            final_url=final_url,
            content_type="text/html",
            body=document.encode(),
        )

    @staticmethod
    def _article_id(url: str) -> str | None:
        match = _ARTICLE_ID.search(urlsplit(url).path)
        return match.group(1).lower() if match else None

    @staticmethod
    def _normalized_path(url: str) -> str:
        return urlsplit(url).path.rstrip("/")
