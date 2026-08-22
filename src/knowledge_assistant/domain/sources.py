"""Source classification and extracted-content contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

from knowledge_assistant.domain.documents import SourceProvider, SourceType
from knowledge_assistant.domain.errors import DomainError

_TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
_X_HOSTS = {
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}
_X_STATUS_PATH = re.compile(
    r"^/(?:[A-Za-z0-9_]{1,50}/status|i/(?:web/)?status)/([0-9]{1,19})(?:/.*)?$"
)


class UnsupportedSourceError(DomainError):
    """The submitted source is not supported by an installed adapter."""


class SourceFetchError(DomainError):
    """A source could not be fetched safely."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class ExtractionError(DomainError):
    """A supported source could not produce a valid article."""


@dataclass(frozen=True, slots=True)
class ClassifiedSource:
    original_url: str
    canonical_url: str
    normalized_source_key: str
    source_type: SourceType
    provider: SourceProvider


@dataclass(frozen=True, slots=True)
class FetchedContent:
    final_url: str
    content_type: str
    body: bytes


@dataclass(frozen=True, slots=True)
class ExtractedImage:
    """An image discovered at a stable placeholder in extracted Markdown."""

    placeholder: str
    original_url: str
    alt_text: str
    expected_width: int | None = None
    expected_height: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractedArticle:
    title: str
    markdown: str
    authors: tuple[str, ...]
    published_at: datetime | None
    canonical_url: str
    images: tuple[ExtractedImage, ...] = ()


class SourceClassifier:
    """Recognize initial article providers and produce stable source keys."""

    def classify(self, url: str) -> ClassifiedSource:
        parsed = urlsplit(url.strip())
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not hostname:
            raise UnsupportedSourceError("Send an absolute HTTP(S) article URL.")
        if parsed.username or parsed.password:
            raise UnsupportedSourceError("URLs containing credentials are not supported.")

        if hostname in _X_HOSTS:
            status_match = _X_STATUS_PATH.fullmatch(parsed.path)
            if status_match is None:
                raise UnsupportedSourceError(
                    "Send an X Article or thread URL containing /status/<post-id>."
                )
            post_id = status_match.group(1)
            canonical_url = f"https://x.com/i/status/{post_id}"
            return ClassifiedSource(
                original_url=url,
                canonical_url=canonical_url,
                normalized_source_key=f"x:post:{post_id}",
                source_type=SourceType.SOCIAL_POST,
                provider=SourceProvider.X,
            )
        if hostname == "medium.com" or hostname.endswith(".medium.com"):
            provider = SourceProvider.MEDIUM
        elif hostname == "substack.com" or hostname.endswith(".substack.com"):
            provider = SourceProvider.SUBSTACK
        else:
            provider = SourceProvider.WEB

        canonical_url = self._canonicalize(parsed)
        digest = hashlib.sha256(f"{provider.value}\0{canonical_url}".encode()).hexdigest()
        return ClassifiedSource(
            original_url=url,
            canonical_url=canonical_url,
            normalized_source_key=f"{provider.value}:sha256:{digest}",
            source_type=SourceType.ARTICLE,
            provider=provider,
        )

    @staticmethod
    def _canonicalize(parsed: SplitResult) -> str:
        split = parsed
        query = [
            (key, value)
            for key, value in parse_qsl(split.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMETERS
        ]
        path = split.path or "/"
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit(
            (
                "https",
                (split.hostname or "").lower(),
                path,
                urlencode(query, doseq=True),
                "",
            )
        )
