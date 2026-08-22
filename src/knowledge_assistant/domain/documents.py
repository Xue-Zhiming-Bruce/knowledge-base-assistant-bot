"""Canonical document entities and value objects."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from urllib.parse import urlsplit

from knowledge_assistant.domain.errors import InvariantViolationError

SCHEMA_VERSION = 2
_IDENTIFIER_PATTERN = re.compile(r"^(doc|rev)_[a-f0-9]{32}$")
_SHA256_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_IMAGE_CONTENT_TYPES = frozenset({"image/gif", "image/jpeg", "image/png", "image/webp"})


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvariantViolationError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class DocumentId:
    value: str

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.value) or not self.value.startswith("doc_"):
            raise InvariantViolationError(
                "document_id must match doc_<32 lowercase hex characters>"
            )

    @classmethod
    def new(cls) -> Self:
        return cls(f"doc_{uuid.uuid4().hex}")

    @classmethod
    def derive_from_source(cls, normalized_source_key: str) -> Self:
        """Derive a stable opaque identity so ingestion retries remain idempotent."""

        digest = hashlib.sha256(normalized_source_key.encode()).hexdigest()[:32]
        return cls(f"doc_{digest}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DeletableArticle:
    """Current registry reference needed for a guarded vault deletion."""

    document_id: DocumentId
    title: str
    vault_path: str


@dataclass(frozen=True, slots=True)
class RevisionId:
    value: str

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.value) or not self.value.startswith("rev_"):
            raise InvariantViolationError(
                "revision_id must match rev_<32 lowercase hex characters>"
            )

    @classmethod
    def derive(
        cls,
        document_id: DocumentId,
        content_fingerprint: str,
        normalizer_version: str,
        schema_version: int = SCHEMA_VERSION,
    ) -> Self:
        material = (
            f"{document_id.value}\0{content_fingerprint}\0{normalizer_version}\0{schema_version}"
        )
        digest = hashlib.sha256(material.encode()).hexdigest()[:32]
        return cls(f"rev_{digest}")

    def __str__(self) -> str:
        return self.value


class SourceType(StrEnum):
    ARTICLE = "article"
    SOCIAL_POST = "social_post"
    PDF = "pdf"
    PODCAST = "podcast"
    VIDEO_TRANSCRIPT = "video_transcript"
    WEB_CONTENT = "web_content"


class SourceProvider(StrEnum):
    SUBSTACK = "substack"
    MEDIUM = "medium"
    X = "x"
    WEB = "web"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class SourceReference:
    url: str
    source_type: SourceType
    provider: SourceProvider

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise InvariantViolationError("source URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise InvariantViolationError("source URL must not contain credentials")


@dataclass(frozen=True, slots=True)
class IngestionProvenance:
    extractor: str
    extractor_version: str
    normalizer_version: str

    def __post_init__(self) -> None:
        for name, value in (
            ("extractor", self.extractor),
            ("extractor_version", self.extractor_version),
            ("normalizer_version", self.normalizer_version),
        ):
            if not value.strip():
                raise InvariantViolationError(f"{name} must not be blank")


@dataclass(frozen=True, slots=True)
class DocumentAsset:
    """Canonical metadata for one binary asset referenced by a document."""

    original_url: str
    vault_path: str
    content_type: str
    content_fingerprint: str
    byte_size: int
    width: int
    height: int
    alt_text: str = ""

    def __post_init__(self) -> None:
        parsed = urlsplit(self.original_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise InvariantViolationError("asset original_url must be an absolute HTTPS URL")
        path_parts = self.vault_path.split("/")
        if (
            len(path_parts) != 3
            or path_parts[0] != "Assets"
            or not _IDENTIFIER_PATTERN.fullmatch(path_parts[1])
            or not path_parts[1].startswith("doc_")
            or any(part in {"", ".", ".."} for part in path_parts)
        ):
            raise InvariantViolationError(
                "asset vault_path must be Assets/<document_id>/<filename>"
            )
        if self.content_type not in _IMAGE_CONTENT_TYPES:
            raise InvariantViolationError("asset content_type is not a supported image type")
        if not _SHA256_PATTERN.fullmatch(self.content_fingerprint):
            raise InvariantViolationError("asset content_fingerprint must use sha256")
        if self.byte_size <= 0:
            raise InvariantViolationError("asset byte_size must be positive")
        if self.width <= 0 or self.height <= 0:
            raise InvariantViolationError("asset dimensions must be positive")


@dataclass(frozen=True, slots=True)
class DocumentRevision:
    document_id: DocumentId
    revision_id: RevisionId
    schema_version: int
    title: str
    source: SourceReference
    source_urls: tuple[str, ...]
    authors: tuple[str, ...]
    published_at: datetime | None
    acquired_at: datetime
    content_fingerprint: str
    language: str
    ingestion: IngestionProvenance
    assets: tuple[DocumentAsset, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version < 1:
            raise InvariantViolationError("schema_version must be positive")
        if not self.title.strip():
            raise InvariantViolationError("title must not be blank")
        if not self.source_urls or self.source.url not in self.source_urls:
            raise InvariantViolationError("source_urls must include the preferred source URL")
        if not _SHA256_PATTERN.fullmatch(self.content_fingerprint):
            raise InvariantViolationError("content_fingerprint must use sha256")
        if not self.language.strip():
            raise InvariantViolationError("language must not be blank")
        _require_utc(self.acquired_at, "acquired_at")
        if self.published_at is not None:
            _require_utc(self.published_at, "published_at")
        if any(asset.vault_path.split("/")[1] != self.document_id.value for asset in self.assets):
            raise InvariantViolationError("asset vault_path must belong to its document")
        asset_paths = tuple(asset.vault_path for asset in self.assets)
        if len(asset_paths) != len(set(asset_paths)):
            raise InvariantViolationError("asset vault_path values must be unique")


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    revision: DocumentRevision
    markdown_body: str

    def __post_init__(self) -> None:
        normalized = self.normalize_body(self.markdown_body)
        if not normalized:
            raise InvariantViolationError("markdown_body must not be blank")
        if normalized != self.markdown_body:
            raise InvariantViolationError("markdown_body must be normalized before construction")
        expected = self.fingerprint(normalized)
        if expected != self.revision.content_fingerprint:
            raise InvariantViolationError("content fingerprint does not match markdown body")

    @classmethod
    def create(
        cls,
        *,
        document_id: DocumentId,
        title: str,
        markdown_body: str,
        source: SourceReference,
        authors: tuple[str, ...] = (),
        published_at: datetime | None = None,
        acquired_at: datetime | None = None,
        language: str = "en",
        ingestion: IngestionProvenance,
        source_urls: tuple[str, ...] | None = None,
        assets: tuple[DocumentAsset, ...] = (),
    ) -> Self:
        body = cls.normalize_body(markdown_body)
        fingerprint = cls.fingerprint(body)
        revision_id = RevisionId.derive(
            document_id,
            fingerprint,
            ingestion.normalizer_version,
        )
        acquired = acquired_at or datetime.now(UTC)
        revision = DocumentRevision(
            document_id=document_id,
            revision_id=revision_id,
            schema_version=SCHEMA_VERSION,
            title=title.strip(),
            source=source,
            source_urls=source_urls or (source.url,),
            authors=tuple(author.strip() for author in authors if author.strip()),
            published_at=published_at,
            acquired_at=acquired,
            content_fingerprint=fingerprint,
            language=language.strip().lower(),
            ingestion=ingestion,
            assets=assets,
        )
        return cls(revision=revision, markdown_body=body)

    @staticmethod
    def normalize_body(body: str) -> str:
        normalized_lines = [line.rstrip() for line in body.replace("\r\n", "\n").split("\n")]
        return (
            "\n".join(normalized_lines).strip() + "\n"
            if any(line.strip() for line in normalized_lines)
            else ""
        )

    @staticmethod
    def fingerprint(body: str) -> str:
        return f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"
