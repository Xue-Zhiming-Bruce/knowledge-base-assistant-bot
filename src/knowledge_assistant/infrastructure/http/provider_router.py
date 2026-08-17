"""Provider-based source acquisition routing."""

from __future__ import annotations

from collections.abc import Mapping

from knowledge_assistant.domain.documents import SourceProvider
from knowledge_assistant.domain.sources import ClassifiedSource, FetchedContent, SourceFetchError
from knowledge_assistant.ports.sources import SourceFetcher


class ProviderSourceFetcher:
    """Route a classified source to its independently replaceable adapter."""

    def __init__(self, fetchers: Mapping[SourceProvider, SourceFetcher]) -> None:
        self._fetchers = dict(fetchers)

    def fetch(self, source: ClassifiedSource) -> FetchedContent:
        fetcher = self._fetchers.get(source.provider)
        if fetcher is None:
            raise SourceFetchError(
                f"No source adapter is installed for provider {source.provider.value}.",
                retryable=False,
            )
        return fetcher.fetch(source)
