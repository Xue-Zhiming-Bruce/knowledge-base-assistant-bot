"""Deterministic domain fixtures."""

from datetime import UTC, datetime

from knowledge_assistant.domain.documents import (
    DocumentAsset,
    DocumentId,
    IngestionProvenance,
    KnowledgeDocument,
    SourceProvider,
    SourceReference,
    SourceType,
)

DOCUMENT_ID = DocumentId("doc_0123456789abcdef0123456789abcdef")


def knowledge_document(
    *,
    title: str = "A Durable Knowledge Document",
    body: str = "A first paragraph.\n\n## Evidence\n\nA supported fact.",
    assets: tuple[DocumentAsset, ...] = (),
) -> KnowledgeDocument:
    return KnowledgeDocument.create(
        document_id=DOCUMENT_ID,
        title=title,
        markdown_body=body,
        source=SourceReference(
            url="https://example.medium.com/a-durable-document",
            source_type=SourceType.ARTICLE,
            provider=SourceProvider.MEDIUM,
        ),
        authors=("Ada Example",),
        published_at=datetime(2026, 7, 1, 8, 0, tzinfo=UTC),
        acquired_at=datetime(2026, 7, 28, 8, 0, tzinfo=UTC),
        language="en",
        ingestion=IngestionProvenance(
            extractor="medium",
            extractor_version="1.0.0",
            normalizer_version="1.0.0",
        ),
        assets=assets,
    )
