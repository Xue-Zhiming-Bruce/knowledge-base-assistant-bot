"""Provider-independent domain model."""

from knowledge_assistant.domain.documents import (
    DocumentId,
    DocumentRevision,
    IngestionProvenance,
    KnowledgeDocument,
    RevisionId,
    SourceProvider,
    SourceReference,
    SourceType,
)
from knowledge_assistant.domain.ingestion import IngestionState

__all__ = [
    "DocumentId",
    "DocumentRevision",
    "IngestionProvenance",
    "IngestionState",
    "KnowledgeDocument",
    "RevisionId",
    "SourceProvider",
    "SourceReference",
    "SourceType",
]
