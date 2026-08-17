"""Application service for materializing extracted article images."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from knowledge_assistant.domain.documents import DocumentAsset, DocumentId
from knowledge_assistant.domain.sources import (
    ExtractedArticle,
    ExtractedImage,
    SourceFetchError,
)
from knowledge_assistant.infrastructure.http.safe_image_fetcher import FetchedImage
from knowledge_assistant.ports.vault import VaultAsset


class ImageFetcher(Protocol):
    def fetch(self, url: str) -> FetchedImage:
        """Fetch and validate one source image."""


@dataclass(frozen=True, slots=True)
class MaterializedArticle:
    markdown: str
    metadata: tuple[DocumentAsset, ...]
    vault_assets: tuple[VaultAsset, ...]
    omitted_images: int


class ArticleAssetMaterializer:
    """Replace image placeholders with portable, content-addressed vault links."""

    def __init__(self, fetcher: ImageFetcher, *, max_images: int = 50) -> None:
        if max_images <= 0:
            raise ValueError("max_images must be positive")
        self._fetcher = fetcher
        self._max_images = max_images

    def materialize(
        self,
        article: ExtractedArticle,
        *,
        document_id: DocumentId,
    ) -> MaterializedArticle:
        markdown = article.markdown
        metadata_by_fingerprint: dict[str, DocumentAsset] = {}
        asset_by_fingerprint: dict[str, VaultAsset] = {}
        omitted = 0

        for ordinal, extracted in enumerate(article.images):
            if ordinal >= self._max_images:
                markdown = self._omit(markdown, extracted.placeholder)
                omitted += 1
                continue
            try:
                fetched = self._fetcher.fetch(extracted.original_url)
                self._validate_dimensions(extracted, fetched)
            except SourceFetchError as error:
                if error.retryable:
                    raise
                markdown = self._omit(markdown, extracted.placeholder)
                omitted += 1
                continue

            digest = fetched.content_fingerprint.removeprefix("sha256:")
            vault_path = PurePosixPath(
                "Assets",
                document_id.value,
                f"{digest}.{fetched.extension}",
            )
            vault_relative_path = vault_path.as_posix()
            markdown = self._rewrite_image_reference(
                markdown,
                placeholder=extracted.placeholder,
                vault_path=vault_relative_path,
            )
            if fetched.content_fingerprint in metadata_by_fingerprint:
                continue
            metadata_by_fingerprint[fetched.content_fingerprint] = DocumentAsset(
                original_url=extracted.original_url,
                vault_path=vault_path.as_posix(),
                content_type=fetched.content_type,
                content_fingerprint=fetched.content_fingerprint,
                byte_size=len(fetched.body),
                width=fetched.width,
                height=fetched.height,
                alt_text=extracted.alt_text,
            )
            asset_by_fingerprint[fetched.content_fingerprint] = VaultAsset(
                vault_path=vault_path,
                content=fetched.body,
                content_fingerprint=fetched.content_fingerprint,
            )

        return MaterializedArticle(
            markdown=markdown,
            metadata=tuple(metadata_by_fingerprint.values()),
            vault_assets=tuple(asset_by_fingerprint.values()),
            omitted_images=omitted,
        )

    @staticmethod
    def _validate_dimensions(
        extracted: ExtractedImage,
        fetched: FetchedImage,
    ) -> None:
        expected_width = extracted.expected_width
        expected_height = extracted.expected_height
        if expected_width is None or expected_height is None:
            return
        if fetched.width < expected_width or fetched.height < expected_height:
            raise SourceFetchError(
                "Downloaded image is smaller than the provider-declared dimensions.",
                retryable=False,
            )

    @staticmethod
    def _omit(markdown: str, placeholder: str) -> str:
        image_pattern = re.compile(rf"!\[[^\]\n]*\]\({re.escape(placeholder)}(?:\s+[^)]*)?\)")
        replaced, count = image_pattern.subn("*[Image omitted during ingestion.]*", markdown)
        if count:
            return replaced
        return markdown.replace(placeholder, "")

    @staticmethod
    def _rewrite_image_reference(
        markdown: str,
        *,
        placeholder: str,
        vault_path: str,
    ) -> str:
        """Create one vault-aware Obsidian embed without source-site wrappers."""

        linked_image_pattern = re.compile(
            rf"\[!\[([^\]\n]*)\]\({re.escape(placeholder)}(?:\s+[^)]*)?\)\]"
            rf"\([^\n)]*\)"
        )
        embed = f"![[{vault_path}]]"
        rewritten = linked_image_pattern.sub(embed, markdown)
        plain_image_pattern = re.compile(rf"!\[[^\]\n]*\]\({re.escape(placeholder)}(?:\s+[^)]*)?\)")
        rewritten = plain_image_pattern.sub(embed, rewritten)
        return rewritten.replace(placeholder, vault_path)
