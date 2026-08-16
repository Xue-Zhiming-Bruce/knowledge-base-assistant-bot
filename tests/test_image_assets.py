from __future__ import annotations

import hashlib
from io import BytesIO

import httpx
import pytest
from PIL import Image

from knowledge_assistant.application.assets import ArticleAssetMaterializer
from knowledge_assistant.domain.documents import DocumentAsset
from knowledge_assistant.domain.sources import (
    ExtractedArticle,
    ExtractedImage,
    SourceFetchError,
)
from knowledge_assistant.infrastructure.http.safe_image_fetcher import (
    FetchedImage,
    SafeImageFetcher,
)
from tests.factories import DOCUMENT_ID


def png_bytes(*, width: int = 4, height: int = 3) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color="blue").save(output, format="PNG")
    return output.getvalue()


def fetched_image(body: bytes | None = None) -> FetchedImage:
    payload = body or png_bytes()
    fingerprint = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    return FetchedImage(
        original_url="https://cdn.example/diagram.png",
        final_url="https://cdn.example/diagram.png",
        content_type="image/png",
        extension="png",
        content_fingerprint=fingerprint,
        body=payload,
        width=4,
        height=3,
    )


class StaticImageFetcher:
    def __init__(self, result: FetchedImage | SourceFetchError) -> None:
        self.result = result
        self.calls = 0

    def fetch(self, _url: str) -> FetchedImage:
        self.calls += 1
        if isinstance(self.result, SourceFetchError):
            raise self.result
        return self.result


def article_with_images(count: int = 1) -> ExtractedArticle:
    images = tuple(
        ExtractedImage(
            placeholder=f"ka-image://{index:04d}",
            original_url=f"https://cdn.example/{index}.png",
            alt_text=f"Diagram {index}",
        )
        for index in range(count)
    )
    markdown = "\n\n".join(
        f"Before {index}\n\n![Diagram {index}]({image.placeholder})\n\nAfter {index}"
        for index, image in enumerate(images)
    )
    return ExtractedArticle(
        title="Images",
        markdown=markdown,
        authors=(),
        published_at=None,
        canonical_url="https://writer.substack.com/p/images",
        images=images,
    )


def test_materializer_rewrites_relative_links_and_deduplicates_content() -> None:
    image = fetched_image()
    fetcher = StaticImageFetcher(image)
    result = ArticleAssetMaterializer(fetcher).materialize(
        article_with_images(2),
        document_id=DOCUMENT_ID,
    )

    assert fetcher.calls == 2
    assert len(result.metadata) == 1
    assert len(result.vault_assets) == 1
    assert result.markdown.count("![[Assets/doc_") == 2
    assert "ka-image://" not in result.markdown
    assert result.omitted_images == 0


def test_materializer_unwraps_clickable_image_for_markdown_portability() -> None:
    article = article_with_images()
    linked_markdown = (
        "[![Diagram 0](ka-image://0000)](https://substackcdn.com/image/fetch/source.png)"
    )
    article = ExtractedArticle(
        title=article.title,
        markdown=linked_markdown,
        authors=article.authors,
        published_at=article.published_at,
        canonical_url=article.canonical_url,
        images=article.images,
    )

    result = ArticleAssetMaterializer(StaticImageFetcher(fetched_image())).materialize(
        article,
        document_id=DOCUMENT_ID,
    )

    assert result.markdown.startswith("![[Assets/")
    assert "substackcdn.com" not in result.markdown
    assert "[![" not in result.markdown


def test_materializer_omits_permanent_image_failure() -> None:
    fetcher = StaticImageFetcher(SourceFetchError("invalid", retryable=False))
    result = ArticleAssetMaterializer(fetcher).materialize(
        article_with_images(),
        document_id=DOCUMENT_ID,
    )

    assert "Image omitted during ingestion" in result.markdown
    assert result.metadata == ()
    assert result.omitted_images == 1


def test_materializer_propagates_retryable_image_failure() -> None:
    fetcher = StaticImageFetcher(SourceFetchError("temporary", retryable=True))
    with pytest.raises(SourceFetchError) as caught:
        ArticleAssetMaterializer(fetcher).materialize(
            article_with_images(),
            document_id=DOCUMENT_ID,
        )
    assert caught.value.retryable


def test_materializer_omits_image_smaller_than_provider_dimensions() -> None:
    article = article_with_images()
    source_image = article.images[0]
    article = ExtractedArticle(
        title=article.title,
        markdown=article.markdown,
        authors=article.authors,
        published_at=article.published_at,
        canonical_url=article.canonical_url,
        images=(
            ExtractedImage(
                placeholder=source_image.placeholder,
                original_url=source_image.original_url,
                alt_text=source_image.alt_text,
                expected_width=100,
                expected_height=100,
            ),
        ),
    )

    result = ArticleAssetMaterializer(StaticImageFetcher(fetched_image())).materialize(
        article, document_id=DOCUMENT_ID
    )

    assert result.metadata == ()
    assert result.omitted_images == 1
    assert "Image omitted during ingestion" in result.markdown


def test_materializer_enforces_image_count_and_constructor_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        ArticleAssetMaterializer(StaticImageFetcher(fetched_image()), max_images=0)

    result = ArticleAssetMaterializer(
        StaticImageFetcher(fetched_image()),
        max_images=1,
    ).materialize(
        article_with_images(2),
        document_id=DOCUMENT_ID,
    )
    assert result.omitted_images == 1
    assert "Image omitted during ingestion" in result.markdown


def test_safe_image_fetcher_validates_real_image_content() -> None:
    payload = png_bytes()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=payload,
            request=request,
        )
    )
    result = SafeImageFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=transport),
    ).fetch("https://cdn.example/diagram")

    assert result.content_type == "image/png"
    assert result.extension == "png"
    assert (result.width, result.height) == (4, 3)


def test_safe_image_fetcher_follows_public_https_redirect() -> None:
    payload = png_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/new"}, request=request)
        return httpx.Response(200, content=payload, request=request)

    result = SafeImageFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).fetch("https://cdn.example/old")

    assert result.final_url == "https://cdn.example/new"


def test_safe_image_fetcher_prefers_original_x_media_variant() -> None:
    payload = png_bytes(width=16, height=9)
    requests: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        return httpx.Response(200, content=payload, request=request)

    source_url = "https://pbs.twimg.com/media/diagram.png"
    result = SafeImageFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).fetch(source_url)

    assert len(requests) == 1
    assert requests[0].path == "/media/diagram"
    assert requests[0].params["format"] == "png"
    assert requests[0].params["name"] == "orig"
    assert result.original_url == source_url
    assert result.final_url.endswith("format=png&name=orig")
    assert (result.width, result.height) == (16, 9)


def test_safe_image_fetcher_falls_back_from_oversized_x_original() -> None:
    payload = png_bytes(width=8, height=6)
    requested_sizes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        size = request.url.params["name"]
        requested_sizes.append(size)
        if size == "orig":
            return httpx.Response(
                200,
                headers={"content-length": "1000"},
                request=request,
            )
        return httpx.Response(200, content=payload, request=request)

    result = SafeImageFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        max_bytes=500,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).fetch("https://pbs.twimg.com/media/diagram.png")

    assert requested_sizes == ["orig", "large"]
    assert result.final_url.endswith("format=png&name=large")
    assert (result.width, result.height) == (8, 6)


def test_safe_image_fetcher_normalizes_x_jpeg_and_ignores_non_media_urls() -> None:
    candidates = SafeImageFetcher._quality_candidates(
        "https://pbs.twimg.com/media/diagram.jpeg?token=kept"
    )

    assert candidates[0].startswith("https://pbs.twimg.com/media/diagram?")
    assert "format=jpg" in candidates[0]
    assert "name=orig" in candidates[0]
    assert "token=kept" in candidates[0]
    profile_url = "https://pbs.twimg.com/profile_images/avatar.jpg"
    assert SafeImageFetcher._quality_candidates(profile_url) == (profile_url,)
    unknown_media_url = "https://pbs.twimg.com/media/diagram.bin"
    assert SafeImageFetcher._quality_candidates(unknown_media_url) == (unknown_media_url,)


def test_safe_image_fetcher_checks_streamed_size_with_invalid_length_header() -> None:
    payload = png_bytes()

    fetcher = SafeImageFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        max_bytes=5,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-length": "unknown"},
                    content=payload,
                    request=request,
                )
            )
        ),
    )

    with pytest.raises(SourceFetchError, match="size limit"):
        fetcher.fetch("https://cdn.example/image.png")


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(429, True), (503, True), (404, False)],
)
def test_safe_image_fetcher_classifies_http_errors(
    status: int,
    retryable: bool,
) -> None:
    fetcher = SafeImageFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        ),
    )
    with pytest.raises(SourceFetchError) as caught:
        fetcher.fetch("https://cdn.example/image.png")
    assert caught.value.retryable is retryable


def test_safe_image_fetcher_rejects_redirect_without_location_and_excessive_size() -> None:
    redirect_fetcher = SafeImageFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(302, request=request))
        ),
    )
    with pytest.raises(SourceFetchError, match="without a location"):
        redirect_fetcher.fetch("https://cdn.example/image.png")

    size_fetcher = SafeImageFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        max_bytes=5,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-length": "100"},
                    content=png_bytes(),
                    request=request,
                )
            )
        ),
    )
    with pytest.raises(SourceFetchError, match="size limit"):
        size_fetcher.fetch("https://cdn.example/image.png")


def test_safe_image_fetcher_rejects_unresolvable_and_oversized_dimensions() -> None:
    def failing_resolver(_host: str) -> tuple[str, ...]:
        raise OSError("DNS unavailable")

    with pytest.raises(SourceFetchError, match="could not be resolved") as caught:
        SafeImageFetcher(resolver=failing_resolver).fetch("https://cdn.example/image.png")
    assert caught.value.retryable

    with pytest.raises(SourceFetchError, match="no addresses"):
        SafeImageFetcher(resolver=lambda _host: ()).fetch("https://cdn.example/image.png")

    fetcher = SafeImageFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        max_pixels=2,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=png_bytes(),
                    request=request,
                )
            )
        ),
    )
    with pytest.raises(SourceFetchError, match="dimensions"):
        fetcher.fetch("https://cdn.example/image.png")


@pytest.mark.parametrize(
    ("url", "address", "message"),
    [
        ("http://cdn.example/image.png", "93.184.216.34", "public HTTPS"),
        ("https://cdn.example/image.png", "127.0.0.1", "non-public"),
    ],
)
def test_safe_image_fetcher_rejects_unsafe_network_targets(
    url: str,
    address: str,
    message: str,
) -> None:
    fetcher = SafeImageFetcher(resolver=lambda _host: (address,))
    with pytest.raises(SourceFetchError, match=message):
        fetcher.fetch(url)


def test_safe_image_fetcher_rejects_invalid_image_bytes() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"<svg/>", request=request)
    )
    fetcher = SafeImageFetcher(
        resolver=lambda _host: ("93.184.216.34",),
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(SourceFetchError, match="invalid"):
        fetcher.fetch("https://cdn.example/image.svg")


def asset_metadata(image: FetchedImage) -> DocumentAsset:
    return DocumentAsset(
        original_url=image.original_url,
        vault_path=(
            f"Assets/{DOCUMENT_ID.value}/{image.content_fingerprint.removeprefix('sha256:')}.png"
        ),
        content_type=image.content_type,
        content_fingerprint=image.content_fingerprint,
        byte_size=len(image.body),
        width=image.width,
        height=image.height,
        alt_text="Diagram",
    )
