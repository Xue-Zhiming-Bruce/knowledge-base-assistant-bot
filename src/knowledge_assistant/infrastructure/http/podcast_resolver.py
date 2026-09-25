"""Podcast episode audio/metadata resolvers for supported platforms.

Supported today: Xiaoyuzhou FM (小宇宙), Apple Podcasts episodes, and Substack
podcast posts. Each resolver turns an episode page/API response into a
``PodcastEpisode`` carrying a publicly accessible audio URL.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
from datetime import UTC, datetime
from typing import cast
from urllib.parse import parse_qs, urlsplit

import httpx
from markdownify import markdownify

from knowledge_assistant.domain.podcasts import PodcastEpisode
from knowledge_assistant.domain.sources import ClassifiedSource, SourceFetchError
from knowledge_assistant.infrastructure.http.safe_fetcher import (
    AddressResolver,
    _resolve_addresses,
)

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MAX_PAGE_BYTES = 8_000_000
_MAX_AUDIO_BYTES = 500_000_000
_AUDIO_CONTENT_PREFIXES = ("audio/",)
_AUDIO_CONTENT_EXACT = {
    "application/ogg",
    "application/octet-stream",
    "binary/octet-stream",
}


class PodcastAudioDownloader:
    """Stream a podcast audio file with SSRF and size protections."""

    def __init__(
        self,
        *,
        resolver: AddressResolver = _resolve_addresses,
        max_bytes: int = _MAX_AUDIO_BYTES,
        timeout_seconds: float = 120.0,
        max_retries: int = 6,
        client: httpx.Client | None = None,
    ) -> None:
        self._resolver = resolver
        self._max_bytes = max_bytes
        self._max_retries = max_retries
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": _BROWSER_USER_AGENT},
            follow_redirects=False,
        )

    def download(self, url: str) -> tuple[bytes, str]:
        """Return ``(audio_bytes, mime_type)`` or raise a typed fetch error.

        Interrupted downloads resume via HTTP Range instead of restarting:
        podcast CDNs on flaky connections tend to cut large streams.
        """

        body = bytearray()
        mime = "application/octet-stream"
        for attempt in range(self._max_retries + 1):
            try:
                mime, complete = self._stream_once(url, body)
            except (
                httpx.TimeoutException,
                httpx.RemoteProtocolError,
                httpx.NetworkError,
            ) as error:
                if attempt >= self._max_retries or not body:
                    raise SourceFetchError(
                        "Audio download was interrupted or timed out.", retryable=True
                    ) from error
                time.sleep(min(30, 2**attempt))
                continue
            if complete:
                return bytes(body), mime
        return bytes(body), mime

    def _stream_once(self, url: str, body: bytearray) -> tuple[str, bool]:
        """Stream into ``body``, resuming from its current length; return (mime, done)."""

        current_url = url
        headers = {"Range": f"bytes={len(body)}-"} if body else {}
        for _redirect in range(6):
            self._validate_public_https(current_url)
            with self._client.stream("GET", current_url, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise SourceFetchError(
                            "Audio download redirect had no location.",
                            retryable=False,
                        )
                    current_url = location
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    raise SourceFetchError(
                        f"Audio host temporarily returned HTTP {response.status_code}.",
                        retryable=True,
                        status_code=response.status_code,
                    )
                if response.status_code >= 400:
                    raise SourceFetchError(
                        f"Audio host returned HTTP {response.status_code}.",
                        retryable=False,
                        status_code=response.status_code,
                    )
                if response.status_code == 206:
                    content_range = response.headers.get("content-range", "")
                    if content_range and not content_range.startswith(
                        f"bytes {len(body)}-"
                    ):
                        body.clear()  # server resumed from the wrong offset
                elif response.status_code == 200 and body:
                    body.clear()  # server ignored Range; start over
                mime = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                if not self._is_audio_mime(mime):
                    raise SourceFetchError(
                        f"Audio URL returned non-audio content type: {mime or 'unknown'}.",
                        retryable=False,
                    )
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_bytes:
                        raise SourceFetchError(
                            "Audio file exceeds the configured size limit.",
                            retryable=False,
                        )
                return mime, True
            # Timeout/RemoteProtocolError/NetworkError propagate raw so
            # download() can resume with a Range request.
        raise SourceFetchError("Audio download exceeded redirect limit.", retryable=False)

    @staticmethod
    def _is_audio_mime(mime: str) -> bool:
        return mime.startswith(_AUDIO_CONTENT_PREFIXES) or mime in _AUDIO_CONTENT_EXACT

    def _validate_public_https(self, url: str) -> None:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not hostname:
            raise SourceFetchError("Audio URL must be an HTTPS URL.", retryable=False)
        try:
            addresses = self._resolver(hostname)
        except OSError as error:
            raise SourceFetchError(
                "Audio hostname could not be resolved.", retryable=True
            ) from error
        for raw_address in addresses:
            if not ipaddress.ip_address(raw_address).is_global:
                raise SourceFetchError(
                    "Audio hostname resolves to a non-public address.", retryable=False
                )


class PodcastEpisodeResolver:
    """Resolve episode pages/APIs into audio URLs and metadata."""

    def __init__(
        self,
        *,
        downloader: PodcastAudioDownloader | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._downloader = downloader or PodcastAudioDownloader()
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": _BROWSER_USER_AGENT},
            follow_redirects=True,
        )

    def resolve(self, source: ClassifiedSource) -> PodcastEpisode:
        if source.normalized_source_key.startswith("podcast:xiaoyuzhou:"):
            return self.resolve_xiaoyuzhou(source.canonical_url)
        if source.normalized_source_key.startswith("podcast:apple:"):
            return self.resolve_apple(source.canonical_url)
        raise SourceFetchError(
            f"Unsupported podcast source key: {source.normalized_source_key}.",
            retryable=False,
        )

    def resolve_xiaoyuzhou(self, canonical_url: str) -> PodcastEpisode:
        page = self._fetch_text(canonical_url)
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            page,
            re.S,
        )
        if match is None:
            raise SourceFetchError(
                "Xiaoyuzhou episode page did not include episode data.", retryable=True
            )
        try:
            episode = json.loads(match.group(1))["props"]["pageProps"]["episode"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise SourceFetchError(
                "Xiaoyuzhou episode data could not be parsed.", retryable=True
            ) from error
        enclosure = episode.get("enclosure") or {}
        audio_url = enclosure.get("url")
        if not audio_url:
            raise SourceFetchError(
                "This Xiaoyuzhou episode has no publicly accessible audio "
                "(it may be premium or private).",
                retryable=False,
            )
        podcast = episode.get("podcast") or {}
        shownotes_html = episode.get("shownotes") or ""
        return PodcastEpisode(
            title=str(episode.get("title") or "").strip() or "Untitled episode",
            audio_url=audio_url,
            show=(str(podcast.get("title")) if podcast.get("title") else None),
            published_at=_parse_iso(episode.get("pubDate")),
            duration_seconds=_positive_int(episode.get("duration")),
            shownotes_markdown=(
                markdownify(shownotes_html, strip=["script", "style"]).strip()
                if shownotes_html
                else None
            ),
        )

    def resolve_apple(self, canonical_url: str) -> PodcastEpisode:
        parsed = urlsplit(canonical_url)
        episode_id = parse_qs(parsed.query).get("i", [None])[0]
        page = self._fetch_text(canonical_url)
        offer = _find_apple_episode_offer(page, episode_id or "")
        if offer is None:
            raise SourceFetchError(
                "Apple Podcasts episode page did not include audio data.", retryable=True
            )
        enclosure = cast("dict[str, object]", offer.get("currentMediaEnclosure") or {})
        audio_url = str(enclosure.get("streamUrl") or "")
        if not audio_url:
            raise SourceFetchError(
                "Apple Podcasts episode has no streamable audio.", retryable=False
            )
        show_offer = cast(
            "dict[str, object]",
            offer.get("showOffer") or offer.get("podcastOffer") or {},
        )
        # ponytail: Apple's streamUrl can be a preview for some restricted
        # episodes; we trust it. Upgrade by comparing duration to iTunes data.
        return PodcastEpisode(
            title=str(offer.get("title") or "").strip() or "Untitled episode",
            audio_url=audio_url,
            show=(str(show_offer.get("title")) if show_offer.get("title") else None),
            published_at=_parse_iso(offer.get("releaseDate")),
            duration_seconds=_positive_int(enclosure.get("duration")),
        )

    def probe_substack(self, source: ClassifiedSource) -> PodcastEpisode | None:
        """Return a PodcastEpisode when the Substack post is a podcast episode.

        Network failures degrade to None so the article pipeline can proceed.
        """

        parsed = urlsplit(source.canonical_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2 or parts[0] != "p":
            return None
        slug = parts[1]
        api_url = f"https://{parsed.hostname}/api/v1/posts/{slug}?json=true"
        try:
            response = self._client.get(api_url)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            post = response.json()
        except json.JSONDecodeError:
            return None
        audio_url = post.get("podcast_url")
        if not audio_url:
            return None
        published = post.get("post_date") or post.get("published_at")
        return PodcastEpisode(
            title=str(post.get("title") or "").strip() or "Untitled episode",
            audio_url=audio_url,
            published_at=_parse_iso(published),
            duration_seconds=_positive_int(post.get("podcast_duration")),
            shownotes_markdown=(str(post.get("description")) or None)
            if post.get("description")
            else None,
        )

    def download_audio(self, audio_url: str) -> tuple[bytes, str]:
        return self._downloader.download(audio_url)

    def _fetch_text(self, url: str) -> str:
        try:
            response = self._client.get(url)
        except httpx.TimeoutException as error:
            raise SourceFetchError("Episode page fetch timed out.", retryable=True) from error
        except httpx.NetworkError as error:
            raise SourceFetchError(
                "Episode page network error.", retryable=True
            ) from error
        if response.status_code == 429 or response.status_code >= 500:
            raise SourceFetchError(
                f"Episode page temporarily returned HTTP {response.status_code}.",
                retryable=True,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise SourceFetchError(
                f"Episode page returned HTTP {response.status_code}.",
                retryable=False,
                status_code=response.status_code,
            )
        if len(response.content) > _MAX_PAGE_BYTES:
            raise SourceFetchError("Episode page exceeds size limit.", retryable=False)
        return response.text


def _find_apple_episode_offer(page: str, episode_id: str) -> dict[str, object] | None:
    """Scan inline JSON for the episodeOffer whose contentId matches the episode."""

    decoder = json.JSONDecoder()
    for match in re.finditer(r'"episodeOffer"', page):
        start = page.rfind("{", 0, match.start())
        if start == -1:
            continue
        try:
            obj, _end = decoder.raw_decode(page[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        offer = _deep_find_offer(obj, episode_id)
        if offer is not None:
            return offer
    return None


def _deep_find_offer(obj: object, episode_id: str) -> dict[str, object] | None:
    if isinstance(obj, dict):
        if (
            obj.get("contentId") == episode_id
            and "currentMediaEnclosure" in obj
        ):
            return obj
        for value in obj.values():
            found = _deep_find_offer(value, episode_id)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _deep_find_offer(item, episode_id)
            if found is not None:
                return found
    return None


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)
