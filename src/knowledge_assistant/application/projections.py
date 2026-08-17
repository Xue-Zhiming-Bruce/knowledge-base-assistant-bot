"""Full-corpus projection build, validation, and atomic cutover."""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath

from knowledge_assistant.domain.chunks import MarkdownChunker
from knowledge_assistant.infrastructure.postgres.ingestion_repository import (
    PostgresIngestionRepository,
)
from knowledge_assistant.ports.embeddings import EmbeddingProvider
from knowledge_assistant.ports.telemetry import NoOpTelemetry, Telemetry
from knowledge_assistant.ports.vault import VaultRepository


class ProjectionRebuildService:
    """Build every current revision before exposing an incompatible projection."""

    def __init__(
        self,
        *,
        repository: PostgresIngestionRepository,
        vault: VaultRepository,
        chunker: MarkdownChunker,
        embeddings: EmbeddingProvider,
        telemetry: Telemetry | None = None,
    ) -> None:
        self._repository = repository
        self._vault = vault
        self._chunker = chunker
        self._embeddings = embeddings
        self._telemetry = telemetry or NoOpTelemetry()

    def rebuild(self, *, activate: bool = False) -> uuid.UUID:
        documents = self._repository.projection_documents()
        if not documents:
            raise RuntimeError("cannot build a retrieval projection without documents")

        generation_id: uuid.UUID | None = None
        with self._telemetry.span(
            "projection.rebuild",
            {"projection.expected_documents": len(documents)},
        ):
            try:
                for reference in documents:
                    stored = self._vault.read(PurePosixPath(reference.vault_path))
                    if stored.document.revision.revision_id.value != reference.revision_id:
                        raise RuntimeError(
                            f"vault revision changed during rebuild: {reference.document_id}"
                        )
                    chunks = self._chunker.chunk(stored.document)
                    texts = tuple(chunk.content for chunk in chunks)
                    batch = self._embeddings.embed(texts)
                    if generation_id is None:
                        generation_id = self._repository.building_projection_generation(
                            embedding_model=batch.model,
                            dimensions=batch.dimensions,
                            chunker_version=self._chunker.VERSION,
                        )
                    self._repository.store_chunks(
                        generation_id=generation_id,
                        document=stored.document,
                        chunks=chunks,
                        vectors=batch.vectors,
                    )
                assert generation_id is not None
                self._repository.validate_projection_generation(generation_id)
                if activate:
                    self._repository.activate_projection_generation(generation_id)
            except Exception:
                if generation_id is not None:
                    self._repository.fail_projection_generation(generation_id)
                self._telemetry.count(
                    "projection_rebuilds_total",
                    attributes={"outcome": "failed"},
                )
                raise
            self._telemetry.count(
                "projection_documents",
                value=len(documents),
                attributes={"state": "active" if activate else "validated"},
            )
            return generation_id
