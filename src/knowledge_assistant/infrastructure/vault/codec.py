"""Serialization for the canonical Markdown plus YAML frontmatter contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import yaml

from knowledge_assistant.domain.documents import (
    DocumentAsset,
    DocumentId,
    DocumentRevision,
    IngestionProvenance,
    KnowledgeDocument,
    RevisionId,
    SourceProvider,
    SourceReference,
    SourceType,
)
from knowledge_assistant.domain.errors import InvariantViolationError

_DELIMITER = "---"


class KnowledgeDocumentCodec:
    """Encode and decode validated canonical Knowledge Documents."""

    def encode(self, document: KnowledgeDocument) -> bytes:
        revision = document.revision
        frontmatter: dict[str, Any] = {
            "schema_version": revision.schema_version,
            "document_id": revision.document_id.value,
            "revision_id": revision.revision_id.value,
            "title": revision.title,
            "source_type": revision.source.source_type.value,
            "source_provider": revision.source.provider.value,
            "source_url": revision.source.url,
            "source_urls": list(revision.source_urls),
            "authors": list(revision.authors),
            "published_at": (
                revision.published_at.isoformat() if revision.published_at is not None else None
            ),
            "acquired_at": revision.acquired_at.isoformat(),
            "content_fingerprint": revision.content_fingerprint,
            "language": revision.language,
            "ingestion": {
                "extractor": revision.ingestion.extractor,
                "extractor_version": revision.ingestion.extractor_version,
                "normalizer_version": revision.ingestion.normalizer_version,
            },
            "assets": [
                {
                    "original_url": asset.original_url,
                    "vault_path": asset.vault_path,
                    "content_type": asset.content_type,
                    "content_fingerprint": asset.content_fingerprint,
                    "byte_size": asset.byte_size,
                    "width": asset.width,
                    "height": asset.height,
                    "alt_text": asset.alt_text,
                }
                for asset in revision.assets
            ],
        }
        yaml_text = yaml.safe_dump(
            frontmatter,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).rstrip()
        canonical = (
            f"{_DELIMITER}\n{yaml_text}\n{_DELIMITER}\n\n"
            f"# {revision.title}\n\n{document.markdown_body}"
        )
        return canonical.encode("utf-8")

    def decode(self, payload: bytes) -> KnowledgeDocument:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InvariantViolationError("Knowledge Document must be UTF-8") from error

        lines = text.splitlines()
        if not lines or lines[0] != _DELIMITER:
            raise InvariantViolationError("Knowledge Document must start with YAML frontmatter")
        try:
            closing_index = lines.index(_DELIMITER, 1)
        except ValueError as error:
            raise InvariantViolationError("YAML frontmatter is not closed") from error

        try:
            data = yaml.safe_load("\n".join(lines[1:closing_index]))
        except yaml.YAMLError as error:
            raise InvariantViolationError("YAML frontmatter is invalid") from error
        if not isinstance(data, dict):
            raise InvariantViolationError("YAML frontmatter must be a mapping")

        content_lines = lines[closing_index + 1 :]
        while content_lines and not content_lines[0].strip():
            content_lines.pop(0)
        title = self._required_string(data, "title")
        if not content_lines or content_lines[0] != f"# {title}":
            raise InvariantViolationError("Markdown must contain one canonical top-level title")
        body = KnowledgeDocument.normalize_body("\n".join(content_lines[1:]))

        source = SourceReference(
            url=self._required_string(data, "source_url"),
            source_type=SourceType(self._required_string(data, "source_type")),
            provider=SourceProvider(self._required_string(data, "source_provider")),
        )
        ingestion_data = data.get("ingestion")
        if not isinstance(ingestion_data, dict):
            raise InvariantViolationError("ingestion frontmatter must be a mapping")

        revision = DocumentRevision(
            document_id=DocumentId(self._required_string(data, "document_id")),
            revision_id=RevisionId(self._required_string(data, "revision_id")),
            schema_version=self._required_int(data, "schema_version"),
            title=title,
            source=source,
            source_urls=self._string_tuple(data, "source_urls"),
            authors=self._string_tuple(data, "authors"),
            published_at=self._optional_datetime(data, "published_at"),
            acquired_at=self._required_datetime(data, "acquired_at"),
            content_fingerprint=self._required_string(data, "content_fingerprint"),
            language=self._required_string(data, "language"),
            ingestion=IngestionProvenance(
                extractor=self._required_string(ingestion_data, "extractor"),
                extractor_version=self._required_string(ingestion_data, "extractor_version"),
                normalizer_version=self._required_string(ingestion_data, "normalizer_version"),
            ),
            assets=self._assets(data),
        )
        return KnowledgeDocument(revision=revision, markdown_body=body)

    @staticmethod
    def _required_string(data: dict[str, Any], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise InvariantViolationError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _required_int(data: dict[str, Any], key: str) -> int:
        value = data.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvariantViolationError(f"{key} must be an integer")
        return value

    @classmethod
    def _required_datetime(cls, data: dict[str, Any], key: str) -> datetime:
        value = data.get(key)
        if not isinstance(value, str):
            raise InvariantViolationError(f"{key} must be an ISO-8601 string")
        try:
            return datetime.fromisoformat(value)
        except ValueError as error:
            raise InvariantViolationError(f"{key} must be a valid ISO-8601 datetime") from error

    @classmethod
    def _optional_datetime(cls, data: dict[str, Any], key: str) -> datetime | None:
        if data.get(key) is None:
            return None
        return cls._required_datetime(data, key)

    @staticmethod
    def _string_tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
        value = data.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise InvariantViolationError(f"{key} must be a list of strings")
        return tuple(value)

    @classmethod
    def _assets(cls, data: dict[str, Any]) -> tuple[DocumentAsset, ...]:
        value = data.get("assets", [])
        if not isinstance(value, list):
            raise InvariantViolationError("assets must be a list")
        assets: list[DocumentAsset] = []
        for item in value:
            if not isinstance(item, dict):
                raise InvariantViolationError("each asset must be a mapping")
            alt_text = item.get("alt_text", "")
            if not isinstance(alt_text, str):
                raise InvariantViolationError("asset alt_text must be a string")
            assets.append(
                DocumentAsset(
                    original_url=cls._required_string(item, "original_url"),
                    vault_path=cls._required_string(item, "vault_path"),
                    content_type=cls._required_string(item, "content_type"),
                    content_fingerprint=cls._required_string(item, "content_fingerprint"),
                    byte_size=cls._required_int(item, "byte_size"),
                    width=cls._required_int(item, "width"),
                    height=cls._required_int(item, "height"),
                    alt_text=alt_text,
                )
            )
        return tuple(assets)
