"""Bounded public-HTTPS image fetching with content validation."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from PIL import Image, UnidentifiedImageError

from knowledge_assistant.domain.sources import SourceFetchError

AddressResolver = Callable[[str], tuple[str, ...]]

_FORMATS = {
    "GIF": ("image/gif", "gif"),
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}


def _resolve_addresses(hostname: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(result[4][0])
                for result in socket.getaddrinfo(
                    hostname,
                    None,
                    type=socket.SOCK_STREAM,
                )
            }
        )
    )


@dataclass(frozen=True, slots=True)
class FetchedImage:
    original_url: str
    final_url: str
    content_type: str
    extension: str
    content_fingerprint: str
    body: bytes
    width: int
    height: int


class SafeImageFetcher:
    """Fetch and verify an image without allowing private-network access."""

    def __init__(
        self,
        *,
        resolver: AddressResolver = _resolve_addresses,
        max_bytes: int = 10_000_000,
        max_pixels: int = 40_000_000,
        timeout_seconds: float = 20,
        max_redirects: int = 5,
        client: httpx.Client | None = None,
    ) -> None:
        self._resolver = resolver
        self._max_bytes = max_bytes
        self._max_pixels = max_pixels
        self._max_redirects = max_redirects
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "User-Agent": "KnowledgeAssistant/0.1 (+personal knowledge ingestion)",
                "Accept": "image/webp,image/png,image/jpeg,image/gif",
            },
            follow_redirects=False,
        )

    def fetch(self, url: str) -> FetchedImage:
        last_permanent_error: SourceFetchError | None = None
        for candidate_url in self._quality_candidates(url):
            try:
                return self._fetch_candidate(
                    original_url=url,
                    candidate_url=candidate_url,
                )
            except SourceFetchError as error:
                if error.retryable:
                    raise
                last_permanent_error = error
        assert last_permanent_error is not None
        raise last_permanent_error

    def _fetch_candidate(
        self,
        *,
        original_url: str,
        candidate_url: str,
    ) -> FetchedImage:
        current_url = candidate_url
        for redirect_number in range(self._max_redirects + 1):
            self._validate_public_https(current_url)
            try:
                with self._client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise SourceFetchError(
                                "Image returned a redirect without a location.",
                                retryable=False,
                            )
                        if redirect_number >= self._max_redirects:
                            raise SourceFetchError(
                                "Image exceeded the redirect limit.",
                                retryable=False,
                            )
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code == 429 or response.status_code >= 500:
                        raise SourceFetchError(
                            f"Image temporarily returned HTTP {response.status_code}.",
                            retryable=True,
                        )
                    if response.status_code >= 400:
                        raise SourceFetchError(
                            f"Image returned HTTP {response.status_code}.",
                            retryable=False,
                        )
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            declared_length = 0
                        if declared_length > self._max_bytes:
                            raise SourceFetchError(
                                "Image exceeds the configured size limit.",
                                retryable=False,
                            )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self._max_bytes:
                            raise SourceFetchError(
                                "Image exceeds the configured size limit.",
                                retryable=False,
                            )
                    return self._validated_image(
                        original_url=original_url,
                        final_url=str(response.url),
                        body=bytes(body),
                    )
            except httpx.TimeoutException as error:
                raise SourceFetchError("Image request timed out.", retryable=True) from error
            except httpx.NetworkError as error:
                raise SourceFetchError("Image network request failed.", retryable=True) from error
        raise SourceFetchError("Image redirect handling failed.", retryable=False)

    @staticmethod
    def _quality_candidates(url: str) -> tuple[str, ...]:
        """Prefer original X photos while retaining bounded availability fallbacks."""

        parsed = urlsplit(url)
        if (parsed.hostname or "").lower() != "pbs.twimg.com":
            return (url,)
        if not parsed.path.startswith("/media/"):
            return (url,)

        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        path = parsed.path
        suffix = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        image_format = query.get("format", suffix).lower()
        if image_format == "jpeg":
            image_format = "jpg"
        if image_format not in {"jpg", "png", "webp", "gif"}:
            return (url,)
        if suffix in {"jpg", "jpeg", "png", "webp", "gif"}:
            path = path[: -(len(suffix) + 1)]

        def sized(name: str) -> str:
            parameters = {
                key: value for key, value in query.items() if key not in {"format", "name"}
            }
            parameters["format"] = image_format
            parameters["name"] = name
            return urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    path,
                    urlencode(sorted(parameters.items())),
                    "",
                )
            )

        candidates = (sized("orig"), sized("large"), url)
        return tuple(dict.fromkeys(candidates))

    def _validate_public_https(self, url: str) -> None:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
            raise SourceFetchError("Image URL must use public HTTPS.", retryable=False)
        try:
            addresses = self._resolver(hostname)
        except OSError as error:
            raise SourceFetchError(
                "Image hostname could not be resolved.",
                retryable=True,
            ) from error
        if not addresses:
            raise SourceFetchError("Image hostname resolved to no addresses.", retryable=True)
        for raw_address in addresses:
            if not ipaddress.ip_address(raw_address).is_global:
                raise SourceFetchError(
                    "Image hostname resolves to a non-public address.",
                    retryable=False,
                )

    def _validated_image(
        self,
        *,
        original_url: str,
        final_url: str,
        body: bytes,
    ) -> FetchedImage:
        try:
            with Image.open(BytesIO(body)) as image:
                image_format = image.format or ""
                width, height = image.size
                if image_format not in _FORMATS:
                    raise SourceFetchError(
                        "Image format is not supported.",
                        retryable=False,
                    )
                if width <= 0 or height <= 0 or width * height > self._max_pixels:
                    raise SourceFetchError(
                        "Image dimensions exceed the configured limit.",
                        retryable=False,
                    )
                image.verify()
        except SourceFetchError:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError) as error:
            raise SourceFetchError("Downloaded image is invalid.", retryable=False) from error
        content_type, extension = _FORMATS[image_format]
        return FetchedImage(
            original_url=original_url,
            final_url=final_url,
            content_type=content_type,
            extension=extension,
            content_fingerprint=f"sha256:{hashlib.sha256(body).hexdigest()}",
            body=body,
            width=width,
            height=height,
        )
