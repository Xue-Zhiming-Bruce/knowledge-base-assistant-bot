"""HTTP source acquisition adapters."""

from knowledge_assistant.infrastructure.http.medium_feed_fallback import (
    MediumFeedFallbackFetcher,
)
from knowledge_assistant.infrastructure.http.provider_router import ProviderSourceFetcher
from knowledge_assistant.infrastructure.http.safe_fetcher import SafeHttpFetcher
from knowledge_assistant.infrastructure.http.tempo_xquik_article_provider import (
    TempoXquikArticleProvider,
)
from knowledge_assistant.infrastructure.http.x_article_fetcher import XArticleFetcher
from knowledge_assistant.infrastructure.http.xquik_article_provider import (
    XquikArticleProvider,
)

__all__ = [
    "MediumFeedFallbackFetcher",
    "ProviderSourceFetcher",
    "SafeHttpFetcher",
    "TempoXquikArticleProvider",
    "XArticleFetcher",
    "XquikArticleProvider",
]
