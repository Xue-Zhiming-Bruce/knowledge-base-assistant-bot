"""Source acquisition contracts owned by the application boundary."""

from __future__ import annotations

from typing import Protocol

from knowledge_assistant.domain.sources import ClassifiedSource, FetchedContent
from knowledge_assistant.domain.x_articles import XArticleDocument


class SourceFetcher(Protocol):
    """Acquire one classified source as source-neutral content."""

    def fetch(self, source: ClassifiedSource) -> FetchedContent:
        """Fetch one source or raise a typed acquisition error."""


class XArticleProvider(Protocol):
    """Acquire one lossless, ordered X Article from a replaceable provider."""

    def fetch_article(self, post_id: str) -> XArticleDocument:
        """Fetch one Article or raise a typed acquisition error."""
