"""Bounded HTTP fetcher with SSRF and redirect protections."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Collection
from urllib.parse import urljoin, urlsplit

import httpx

from knowledge_assistant.domain.sources import (
    ClassifiedSource,
    FetchedContent,
    SourceFetchError,
)

AddressResolver = Callable[[str], tuple[str, ...]]


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


class SafeHttpFetcher:
    """Fetch one supported public article without following unsafe redirects."""

    def __init__(
        self,
        *,
        resolver: AddressResolver = _resolve_addresses,
        max_bytes: int = 5_000_000,
        timeout_seconds: float = 20,
        max_redirects: int = 5,
        client: httpx.Client | None = None,
    ) -> None:
        self._resolver = resolver
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "User-Agent": "KnowledgeAssistant/0.1 (+personal knowledge ingestion)",
                "Accept": "text/html,application/xhtml+xml",
            },
            follow_redirects=False,
        )

    def fetch(self, source: ClassifiedSource) -> FetchedContent:
        return self.fetch_related(
            source,
            source.canonical_url,
            accepted_content_types={"text/html", "application/xhtml+xml"},
        )

    def fetch_related(
        self,
        source: ClassifiedSource,
        url: str,
        *,
        accepted_content_types: Collection[str],
    ) -> FetchedContent:
        """Fetch a provider-owned related resource under the same safety policy."""

        current_url = url
        original_host = self._validated_host(current_url)
        verified_custom_host: str | None = None

        for redirect_number in range(self._max_redirects + 1):
            current_host = self._validated_host(current_url)
            on_provider_host = self._same_provider_host(
                original_host,
                current_host,
                source.provider.value,
            )
            on_custom_substack_host = (
                source.provider.value == "substack"
                and verified_custom_host is not None
                and current_host == verified_custom_host
            )
            if not on_provider_host and not on_custom_substack_host:
                raise SourceFetchError(
                    "Source redirected outside its supported provider domain.",
                    retryable=False,
                )
            try:
                with self._client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise SourceFetchError(
                                "Source returned a redirect without a location.",
                                retryable=False,
                            )
                        if redirect_number >= self._max_redirects:
                            raise SourceFetchError(
                                "Source exceeded the redirect limit.",
                                retryable=False,
                            )
                        next_url = urljoin(current_url, location)
                        next_host = (urlsplit(next_url).hostname or "").lower().rstrip(".")
                        if (
                            source.provider.value == "substack"
                            and on_provider_host
                            and not self._same_provider_host(
                                original_host,
                                next_host,
                                source.provider.value,
                            )
                        ):
                            if urlsplit(next_url).scheme != "https":
                                raise SourceFetchError(
                                    "Substack custom-domain redirects must use HTTPS.",
                                    retryable=False,
                                )
                            verified_custom_host = next_host
                        current_url = next_url
                        continue
                    if response.status_code == 429 or response.status_code >= 500:
                        raise SourceFetchError(
                            f"Source temporarily returned HTTP {response.status_code}.",
                            retryable=True,
                            status_code=response.status_code,
                        )
                    if response.status_code >= 400:
                        raise SourceFetchError(
                            f"Source returned HTTP {response.status_code}.",
                            retryable=False,
                            status_code=response.status_code,
                        )
                    content_type = response.headers.get("content-type", "").split(";")[0].lower()
                    if content_type not in accepted_content_types:
                        raise SourceFetchError(
                            f"Unsupported source content type: {content_type or 'unknown'}.",
                            retryable=False,
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self._max_bytes:
                            raise SourceFetchError(
                                "Source content exceeds the configured size limit.",
                                retryable=False,
                            )
                    if on_custom_substack_host and not self._is_substack_html(bytes(body)):
                        raise SourceFetchError(
                            "Redirect target could not be verified as a Substack publication.",
                            retryable=False,
                        )
                    return FetchedContent(
                        final_url=str(response.url),
                        content_type=content_type,
                        body=bytes(body),
                    )
            except httpx.TimeoutException as error:
                raise SourceFetchError("Source request timed out.", retryable=True) from error
            except httpx.NetworkError as error:
                raise SourceFetchError("Source network request failed.", retryable=True) from error

        raise SourceFetchError("Source redirect handling failed.", retryable=False)

    def _validated_host(self, url: str) -> str:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not hostname:
            raise SourceFetchError("Unsafe source URL.", retryable=False)
        try:
            addresses = self._resolver(hostname)
        except OSError as error:
            raise SourceFetchError(
                "Source hostname could not be resolved.",
                retryable=True,
            ) from error
        if not addresses:
            raise SourceFetchError("Source hostname resolved to no addresses.", retryable=True)
        for raw_address in addresses:
            address = ipaddress.ip_address(raw_address)
            if not address.is_global:
                raise SourceFetchError(
                    "Source hostname resolves to a non-public address.",
                    retryable=False,
                )
        return hostname

    @staticmethod
    def _same_provider_host(original: str, current: str, provider: str) -> bool:
        if provider == "web":
            # ponytail: apex/www treated as one host; per-site redirect maps only if needed
            return original.removeprefix("www.") == current.removeprefix("www.")
        suffix = ".medium.com" if provider == "medium" else ".substack.com"
        root = suffix.removeprefix(".")
        return (original == root or original.endswith(suffix)) and (
            current == root or current.endswith(suffix)
        )

    @staticmethod
    def _is_substack_html(body: bytes) -> bool:
        sample = body.lower()
        return b"substackcdn.com" in sample and b"publication_id" in sample
