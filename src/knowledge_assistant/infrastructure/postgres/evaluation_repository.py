"""Read-only PostgreSQL evaluation corpus adapter."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from knowledge_assistant.domain.evaluation import EvaluationChunk


class PostgresEvaluationRepository:
    def __init__(
        self,
        database_url: str,
        *,
        pool: ConnectionPool[Any] | None = None,
    ) -> None:
        conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self._pool = pool or ConnectionPool(
            conninfo,
            min_size=1,
            max_size=4,
            kwargs={"row_factory": dict_row},
        )

    def close(self) -> None:
        self._pool.close()

    def active_generation_id(self) -> uuid.UUID:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT generation_id
                FROM projection_generations
                WHERE state = 'active'
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("no active retrieval projection exists")
        return uuid.UUID(str(row["generation_id"]))

    def sample_chunks(
        self,
        *,
        generation_id: uuid.UUID,
        count: int,
        seed: str,
        minimum_tokens: int = 80,
        maximum_tokens: int = 500,
        max_per_document: int = 2,
    ) -> tuple[EvaluationChunk, ...]:
        if count < 1 or max_per_document < 1:
            raise ValueError("sample count and per-document limit must be positive")
        if minimum_tokens < 1 or maximum_tokens < minimum_tokens:
            raise ValueError("invalid evaluation token range")
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                WITH eligible AS (
                    SELECT
                        chunk.*,
                        revision.source_provider,
                        row_number() OVER (
                            PARTITION BY chunk.document_id
                            ORDER BY md5(%s || ':' || chunk.chunk_id), chunk.chunk_id
                        ) AS document_rank
                    FROM chunks AS chunk
                    JOIN documents AS document
                      ON document.document_id = chunk.document_id
                     AND document.current_revision_id = chunk.revision_id
                    JOIN document_revisions AS revision
                      ON revision.revision_id = chunk.revision_id
                    WHERE chunk.generation_id = %s
                      AND chunk.token_count BETWEEN %s AND %s
                )
                SELECT *
                FROM eligible
                WHERE document_rank <= %s
                ORDER BY md5(%s || ':' || chunk_id), chunk_id
                LIMIT %s
                """,
                (
                    seed,
                    generation_id,
                    minimum_tokens,
                    maximum_tokens,
                    max_per_document,
                    seed,
                    count,
                ),
            ).fetchall()
        return tuple(
            EvaluationChunk(
                generation_id=str(generation_id),
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                revision_id=row["revision_id"],
                content=row["content"],
                content_fingerprint=row["content_fingerprint"],
                token_count=row["token_count"],
                source_provider=row["source_provider"],
            )
            for row in rows
        )

    def validate_chunk(
        self,
        *,
        generation_id: uuid.UUID,
        chunk_id: str,
        content_fingerprint: str,
    ) -> bool:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM chunks AS chunk
                JOIN documents AS document
                  ON document.document_id = chunk.document_id
                 AND document.current_revision_id = chunk.revision_id
                WHERE chunk.generation_id = %s
                  AND chunk.chunk_id = %s
                  AND chunk.content_fingerprint = %s
                """,
                (generation_id, chunk_id, content_fingerprint),
            ).fetchone()
        return row is not None

    def document_chunks(
        self,
        *,
        generation_id: uuid.UUID,
        document_id: str,
    ) -> tuple[str, ...]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT chunk.chunk_id
                FROM chunks AS chunk
                JOIN documents AS document
                  ON document.document_id = chunk.document_id
                 AND document.current_revision_id = chunk.revision_id
                WHERE chunk.generation_id = %s
                  AND chunk.document_id = %s
                ORDER BY chunk.ordinal
                """,
                (generation_id, document_id),
            ).fetchall()
        return tuple(str(row["chunk_id"]) for row in rows)

    def document_id_for_url(self, *, url: str) -> str | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT document_source.document_id
                FROM document_sources AS document_source
                JOIN documents AS document
                  ON document.document_id = document_source.document_id
                 AND document.current_revision_id IS NOT NULL
                WHERE document_source.source_url = %s
                ORDER BY document_source.first_seen_at DESC
                LIMIT 1
                """,
                (url,),
            ).fetchone()
        return str(row["document_id"]) if row else None
