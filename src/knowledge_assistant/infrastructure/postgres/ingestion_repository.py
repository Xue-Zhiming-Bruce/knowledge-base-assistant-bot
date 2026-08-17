"""PostgreSQL repositories for durable ingestion and projections."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from knowledge_assistant.domain.chunks import DocumentChunk
from knowledge_assistant.domain.documents import DeletableArticle, DocumentId, KnowledgeDocument
from knowledge_assistant.domain.errors import ProjectionRebuildRequiredError
from knowledge_assistant.domain.ingestion import IngestionState
from knowledge_assistant.domain.sources import ClassifiedSource
from knowledge_assistant.ports.vault import StoredKnowledgeDocument


@dataclass(frozen=True, slots=True)
class JobSubmission:
    job_id: uuid.UUID
    state: IngestionState
    created: bool


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: uuid.UUID
    source_url: str
    normalized_source_key: str
    source_type: str
    source_provider: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class JobSubscriber:
    recipient_key: str
    request_message_id: str


@dataclass(frozen=True, slots=True)
class PendingNotification:
    notification_id: uuid.UUID
    recipient_key: str
    request_message_id: str
    message: str


@dataclass(frozen=True, slots=True)
class ProjectionDocument:
    document_id: str
    revision_id: str
    vault_path: str


class PostgresIngestionRepository:
    """Coordinate idempotent submissions, worker claims, and derived writes."""

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
            max_size=8,
            kwargs={"row_factory": dict_row},
        )

    def close(self) -> None:
        self._pool.close()

    def submit(
        self,
        *,
        idempotency_key: str,
        source: ClassifiedSource,
        recipient_key: str | None = None,
        request_message_id: str | None = None,
    ) -> JobSubmission:
        """Submit an idempotent ingestion job.

        ``recipient_key=None`` records no notification subscriber: the job is
        ingested without any completion notification, represented explicitly
        and safely (no fake recipient such as chat ID 0).
        """
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (source.normalized_source_key,),
            )
            row = connection.execute(
                """
                SELECT job_id, state
                FROM ingestion_jobs
                WHERE idempotency_key = %s
                """,
                (idempotency_key,),
            ).fetchone()
            created = False
            if row is None:
                row = connection.execute(
                    """
                    SELECT job_id, state
                    FROM ingestion_jobs
                    WHERE normalized_source_key = %s
                      AND state NOT IN ('ready', 'rejected', 'failed')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (source.normalized_source_key,),
                ).fetchone()
            if row is None:
                job_id = uuid.uuid4()
                row = connection.execute(
                    """
                    INSERT INTO ingestion_jobs (
                        job_id,
                        idempotency_key,
                        normalized_source_key,
                        source_url,
                        source_type,
                        source_provider,
                        state
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'queued')
                    RETURNING job_id, state
                    """,
                    (
                        job_id,
                        idempotency_key,
                        source.normalized_source_key,
                        source.canonical_url,
                        source.source_type.value,
                        source.provider.value,
                    ),
                ).fetchone()
                created = True
            assert row is not None
            if recipient_key is not None:
                connection.execute(
                    """
                    INSERT INTO ingestion_subscribers (
                        job_id, client_type, recipient_key, request_message_id
                    )
                    VALUES (%s, 'telegram', %s, COALESCE(%s, '0'))
                    ON CONFLICT DO NOTHING
                    """,
                    (row["job_id"], recipient_key, request_message_id),
                )
            return JobSubmission(
                job_id=row["job_id"],
                state=IngestionState(row["state"]),
                created=created,
            )

    def claim(self, *, worker_id: str, lease_seconds: int = 120) -> ClaimedJob | None:
        with self._pool.connection() as connection, connection.transaction():
            row = connection.execute(
                """
                SELECT *
                FROM ingestion_jobs
                WHERE (
                    state = 'queued'
                    OR (state = 'retry_scheduled' AND next_attempt_at <= now())
                    OR (
                        state IN (
                            'fetching', 'extracting', 'normalizing',
                            'validating', 'committing', 'indexing'
                        )
                        AND lease_expires_at < now()
                    )
                )
                  AND (
                    state NOT IN (
                        'fetching', 'extracting', 'normalizing',
                        'validating', 'committing', 'indexing'
                    )
                    OR lease_expires_at < now()
                  )
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE ingestion_jobs
                SET state = 'fetching',
                    lease_owner = %s,
                    lease_expires_at = now() + (%s * interval '1 second'),
                    attempt_count = attempt_count + 1,
                    updated_at = now(),
                    error_class = NULL,
                    error_code = NULL,
                    error_detail = NULL
                WHERE job_id = %s
                RETURNING *
                """,
                (worker_id, lease_seconds, row["job_id"]),
            ).fetchone()
            assert updated is not None
            return ClaimedJob(
                job_id=updated["job_id"],
                source_url=updated["source_url"],
                normalized_source_key=updated["normalized_source_key"],
                source_type=updated["source_type"],
                source_provider=updated["source_provider"],
                attempt_count=updated["attempt_count"],
            )

    def transition(
        self,
        job_id: uuid.UUID,
        *,
        expected: IngestionState,
        target: IngestionState,
    ) -> None:
        expected.transition_to(target)
        with self._pool.connection() as connection:
            result = connection.execute(
                """
                UPDATE ingestion_jobs
                SET state = %s, updated_at = now()
                WHERE job_id = %s AND state = %s
                """,
                (target.value, job_id, expected.value),
            )
            if result.rowcount != 1:
                raise RuntimeError(
                    f"job {job_id} did not transition from {expected.value} to {target.value}"
                )

    def find_document_id(self, normalized_source_key: str) -> DocumentId | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT document_id
                FROM document_sources
                WHERE normalized_source_key = %s
                """,
                (normalized_source_key,),
            ).fetchone()
        return DocumentId(row["document_id"]) if row is not None else None

    def register_document(
        self,
        *,
        source: ClassifiedSource,
        stored: StoredKnowledgeDocument,
    ) -> None:
        revision = stored.document.revision
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                """
                INSERT INTO documents (document_id)
                VALUES (%s)
                ON CONFLICT (document_id) DO NOTHING
                """,
                (revision.document_id.value,),
            )
            connection.execute(
                """
                INSERT INTO document_sources (
                    document_id,
                    normalized_source_key,
                    source_url,
                    source_type,
                    source_provider,
                    last_checked_at
                )
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (normalized_source_key)
                DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    last_checked_at = now()
                """,
                (
                    revision.document_id.value,
                    source.normalized_source_key,
                    revision.source.url,
                    revision.source.source_type.value,
                    revision.source.provider.value,
                ),
            )
            connection.execute(
                """
                INSERT INTO document_revisions (
                    revision_id,
                    document_id,
                    schema_version,
                    vault_path,
                    title,
                    source_url,
                    source_urls,
                    source_type,
                    source_provider,
                    authors,
                    published_at,
                    acquired_at,
                    content_fingerprint,
                    file_fingerprint,
                    language,
                    ingestion_provenance,
                    assets
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb,
                    %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb
                )
                ON CONFLICT (revision_id)
                DO UPDATE SET
                    file_fingerprint = EXCLUDED.file_fingerprint,
                    assets = EXCLUDED.assets
                """,
                (
                    revision.revision_id.value,
                    revision.document_id.value,
                    revision.schema_version,
                    stored.vault_path.as_posix(),
                    revision.title,
                    revision.source.url,
                    json.dumps(revision.source_urls),
                    revision.source.source_type.value,
                    revision.source.provider.value,
                    json.dumps(revision.authors),
                    revision.published_at,
                    revision.acquired_at,
                    revision.content_fingerprint,
                    stored.file_fingerprint,
                    revision.language,
                    json.dumps(
                        {
                            "extractor": revision.ingestion.extractor,
                            "extractor_version": revision.ingestion.extractor_version,
                            "normalizer_version": revision.ingestion.normalizer_version,
                        }
                    ),
                    json.dumps(
                        [
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
                        ]
                    ),
                ),
            )
            connection.execute(
                """
                UPDATE documents
                SET current_revision_id = %s, updated_at = now()
                WHERE document_id = %s
                """,
                (revision.revision_id.value, revision.document_id.value),
            )

    def ensure_projection_generation(
        self,
        *,
        embedding_model: str,
        dimensions: int,
        chunker_version: str,
    ) -> uuid.UUID:
        manifest = self._projection_manifest(
            embedding_model=embedding_model,
            dimensions=dimensions,
            chunker_version=chunker_version,
        )
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('projection-generation', 0))"
            )
            row = connection.execute(
                """
                SELECT generation_id
                FROM projection_generations
                WHERE state = 'active'
                  AND compatibility_manifest = %s::jsonb
                LIMIT 1
                """,
                (json.dumps(manifest),),
            ).fetchone()
            if row is not None:
                return uuid.UUID(str(row["generation_id"]))
            active = connection.execute(
                """
                SELECT generation_id
                FROM projection_generations
                WHERE state = 'active'
                """
            ).fetchone()
            generation_id = uuid.uuid4()
            if active is None:
                connection.execute(
                    """
                    INSERT INTO projection_generations (
                        generation_id, state, compatibility_manifest,
                        expected_document_count, activated_at
                    )
                    VALUES (
                        %s, 'active', %s::jsonb,
                        (SELECT count(*) FROM documents), now()
                    )
                    """,
                    (generation_id, json.dumps(manifest)),
                )
                return generation_id
            candidate = connection.execute(
                """
                SELECT generation_id
                FROM projection_generations
                WHERE state = 'building'
                  AND compatibility_manifest = %s::jsonb
                LIMIT 1
                """,
                (json.dumps(manifest),),
            ).fetchone()
            if candidate is None:
                connection.execute(
                    """
                    INSERT INTO projection_generations (
                        generation_id, state, compatibility_manifest,
                        expected_document_count
                    )
                    VALUES (
                        %s, 'building', %s::jsonb,
                        (SELECT count(*) FROM documents)
                    )
                    """,
                    (generation_id, json.dumps(manifest)),
                )
            else:
                generation_id = uuid.UUID(str(candidate["generation_id"]))
        raise ProjectionRebuildRequiredError(
            "retrieval projection is incompatible; run projection-rebuild "
            f"for candidate {generation_id}"
        )

    def store_chunks(
        self,
        *,
        generation_id: uuid.UUID,
        document: KnowledgeDocument,
        chunks: tuple[DocumentChunk, ...],
        vectors: tuple[tuple[float, ...], ...],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                """
                DELETE FROM chunks
                WHERE generation_id = %s AND revision_id = %s
                """,
                (generation_id, document.revision.revision_id.value),
            )
            for chunk, vector in zip(chunks, vectors, strict=True):
                vector_literal = "[" + ",".join(str(value) for value in vector) + "]"
                citation_anchor = {
                    "revision_id": document.revision.revision_id.value,
                    "chunk_id": chunk.chunk_id,
                    "heading_path": chunk.heading_path,
                    "content_fingerprint": chunk.content_fingerprint,
                }
                connection.execute(
                    """
                    INSERT INTO chunks (
                        generation_id,
                        chunk_id,
                        document_id,
                        revision_id,
                        ordinal,
                        content,
                        content_fingerprint,
                        heading_path,
                        citation_anchor,
                        token_count,
                        embedding,
                        search_vector
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                        %s, %s::vector, to_tsvector('simple', %s)
                    )
                    """,
                    (
                        generation_id,
                        chunk.chunk_id,
                        document.revision.document_id.value,
                        document.revision.revision_id.value,
                        chunk.ordinal,
                        chunk.content,
                        chunk.content_fingerprint,
                        json.dumps(chunk.heading_path),
                        json.dumps(citation_anchor),
                        chunk.token_count,
                        vector_literal,
                        chunk.content,
                    ),
                )
            connection.execute(
                """
                UPDATE projection_generations AS generation
                SET indexed_document_count = (
                    SELECT count(DISTINCT chunk.document_id)
                    FROM chunks AS chunk
                    WHERE chunk.generation_id = generation.generation_id
                )
                WHERE generation_id = %s
                """,
                (generation_id,),
            )

    def projection_documents(self) -> tuple[ProjectionDocument, ...]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    document.document_id,
                    revision.revision_id,
                    revision.vault_path
                FROM documents AS document
                JOIN document_revisions AS revision
                  ON revision.revision_id = document.current_revision_id
                ORDER BY document.document_id
                """
            ).fetchall()
        return tuple(
            ProjectionDocument(
                document_id=row["document_id"],
                revision_id=row["revision_id"],
                vault_path=row["vault_path"],
            )
            for row in rows
        )

    def find_articles_by_title(
        self,
        title: str,
        *,
        exact: bool,
        limit: int = 5,
    ) -> tuple[DeletableArticle, ...]:
        """Find current articles without allowing SQL wildcard injection."""

        if limit <= 0:
            raise ValueError("limit must be positive")
        normalized = " ".join(title.split())
        predicate = (
            "lower(btrim(revision.title)) = lower(btrim(%s))"
            if exact
            else "strpos(lower(revision.title), lower(%s)) > 0"
        )
        with self._pool.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT document.document_id, revision.title, revision.vault_path
                FROM documents AS document
                JOIN document_revisions AS revision
                  ON revision.revision_id = document.current_revision_id
                WHERE {predicate}
                ORDER BY lower(revision.title), document.document_id
                LIMIT %s
                """,
                (normalized, limit),
            ).fetchall()
        return tuple(
            DeletableArticle(
                document_id=DocumentId(row["document_id"]),
                title=row["title"],
                vault_path=row["vault_path"],
            )
            for row in rows
        )

    def delete_document(
        self,
        document_id: DocumentId,
        *,
        delete_from_vault: Callable[[], None],
    ) -> bool:
        """Delete registry state and every derived chunk for one document."""

        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "SELECT document_id FROM documents WHERE document_id = %s FOR UPDATE",
                (document_id.value,),
            )
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET document_id = NULL, revision_id = NULL, updated_at = now()
                WHERE document_id = %s
                """,
                (document_id.value,),
            )
            deleted = connection.execute(
                "DELETE FROM documents WHERE document_id = %s RETURNING document_id",
                (document_id.value,),
            ).fetchone()
            if deleted is None:
                return False
            connection.execute(
                """
                UPDATE projection_generations AS generation
                SET indexed_document_count = (
                    SELECT count(DISTINCT chunk.document_id)
                    FROM chunks AS chunk
                    WHERE chunk.generation_id = generation.generation_id
                )
                """
            )
            delete_from_vault()
        return True

    def building_projection_generation(
        self,
        *,
        embedding_model: str,
        dimensions: int,
        chunker_version: str,
    ) -> uuid.UUID:
        manifest = self._projection_manifest(
            embedding_model=embedding_model,
            dimensions=dimensions,
            chunker_version=chunker_version,
        )
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('projection-generation', 0))"
            )
            row = connection.execute(
                """
                SELECT generation_id
                FROM projection_generations
                WHERE state = 'building'
                  AND compatibility_manifest = %s::jsonb
                LIMIT 1
                """,
                (json.dumps(manifest),),
            ).fetchone()
            if row is not None:
                return uuid.UUID(str(row["generation_id"]))
            generation_id = uuid.uuid4()
            connection.execute(
                """
                INSERT INTO projection_generations (
                    generation_id, state, compatibility_manifest,
                    expected_document_count
                )
                VALUES (
                    %s, 'building', %s::jsonb,
                    (SELECT count(*) FROM documents)
                )
                """,
                (generation_id, json.dumps(manifest)),
            )
            return generation_id

    @staticmethod
    def _projection_manifest(
        *,
        embedding_model: str,
        dimensions: int,
        chunker_version: str,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "embedding_model": embedding_model,
            "embedding_dimensions": dimensions,
            "chunker_version": chunker_version,
        }

    def validate_projection_generation(self, generation_id: uuid.UUID) -> None:
        with self._pool.connection() as connection, connection.transaction():
            result = connection.execute(
                """
                UPDATE projection_generations AS generation
                SET state = 'validated'
                WHERE generation_id = %s
                  AND state = 'building'
                  AND indexed_document_count = expected_document_count
                  AND NOT EXISTS (
                      SELECT 1
                      FROM documents AS document
                      WHERE NOT EXISTS (
                          SELECT 1
                          FROM chunks AS chunk
                          WHERE chunk.generation_id = generation.generation_id
                            AND chunk.document_id = document.document_id
                            AND chunk.revision_id = document.current_revision_id
                      )
                  )
                """,
                (generation_id,),
            )
            if result.rowcount != 1:
                raise RuntimeError("projection generation is incomplete or not building")

    def activate_projection_generation(self, generation_id: uuid.UUID) -> None:
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('projection-generation', 0))"
            )
            candidate = connection.execute(
                """
                SELECT generation_id
                FROM projection_generations
                WHERE generation_id = %s AND state = 'validated'
                FOR UPDATE
                """,
                (generation_id,),
            ).fetchone()
            if candidate is None:
                raise RuntimeError("only a validated projection can be activated")
            connection.execute(
                """
                UPDATE projection_generations
                SET state = 'retired', retired_at = now()
                WHERE state = 'active'
                """
            )
            connection.execute(
                """
                UPDATE projection_generations
                SET state = 'active', activated_at = now(), retired_at = NULL
                WHERE generation_id = %s
                """,
                (generation_id,),
            )

    def fail_projection_generation(self, generation_id: uuid.UUID) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                UPDATE projection_generations
                SET state = 'failed'
                WHERE generation_id = %s AND state = 'building'
                """,
                (generation_id,),
            )

    def mark_ready(
        self,
        job_id: uuid.UUID,
        *,
        document: KnowledgeDocument,
    ) -> None:
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"ingestion-ready:{job_id}")
        with self._pool.connection() as connection, connection.transaction():
            result = connection.execute(
                """
                UPDATE ingestion_jobs
                SET state = 'ready',
                    document_id = %s,
                    revision_id = %s,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE job_id = %s AND state = 'indexing'
                """,
                (
                    document.revision.document_id.value,
                    document.revision.revision_id.value,
                    job_id,
                ),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"job {job_id} could not be marked ready")
            payload = {
                "job_id": str(job_id),
                "document_id": document.revision.document_id.value,
                "revision_id": document.revision.revision_id.value,
                "title": document.revision.title,
                "source_url": document.revision.source.url,
            }
            connection.execute(
                """
                INSERT INTO outbox_events (
                    event_id, aggregate_type, aggregate_id, event_type,
                    event_version, payload, occurred_at, published_at
                )
                VALUES (%s, 'ingestion_job', %s, 'ingestion.ready', 1, %s::jsonb, now(), now())
                ON CONFLICT (event_id) DO NOTHING
                """,
                (event_id, str(job_id), json.dumps(payload)),
            )
            subscribers = connection.execute(
                """
                SELECT recipient_key, request_message_id
                FROM ingestion_subscribers
                WHERE job_id = %s AND client_type = 'telegram'
                """,
                (job_id,),
            ).fetchall()
            for subscriber in subscribers:
                notification_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{event_id}:telegram:{subscriber['recipient_key']}",
                )
                connection.execute(
                    """
                    INSERT INTO notification_deliveries (
                        notification_id, event_id, client_type, recipient_key,
                        idempotency_key, state, next_attempt_at
                    )
                    VALUES (%s, %s, 'telegram', %s, %s, 'pending', now())
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """,
                    (
                        notification_id,
                        event_id,
                        subscriber["recipient_key"],
                        f"telegram:{event_id}:{subscriber['recipient_key']}",
                    ),
                )

    def fail_or_retry(
        self,
        job: ClaimedJob,
        *,
        error: Exception,
        retryable: bool,
        max_attempts: int = 3,
    ) -> IngestionState:
        should_retry = retryable and job.attempt_count < max_attempts
        target = IngestionState.RETRY_SCHEDULED if should_retry else IngestionState.FAILED
        delay_seconds = min(300, 5 * (2 ** max(0, job.attempt_count - 1)))
        with self._pool.connection() as connection:
            connection.execute(
                """
                UPDATE ingestion_jobs
                SET state = %s,
                    next_attempt_at = %s,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error_class = %s,
                    error_code = %s,
                    error_detail = %s::jsonb,
                    updated_at = now()
                WHERE job_id = %s
                """,
                (
                    target.value,
                    (
                        datetime.now(UTC) + timedelta(seconds=delay_seconds)
                        if should_retry
                        else None
                    ),
                    type(error).__name__,
                    "ingestion_failed",
                    json.dumps({"message": str(error)[:1000]}),
                    job.job_id,
                ),
            )
        return target

    def subscribers(self, job_id: uuid.UUID) -> tuple[JobSubscriber, ...]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT recipient_key, request_message_id
                FROM ingestion_subscribers
                WHERE job_id = %s AND client_type = 'telegram'
                """,
                (job_id,),
            ).fetchall()
        return tuple(
            JobSubscriber(
                recipient_key=row["recipient_key"],
                request_message_id=row["request_message_id"],
            )
            for row in rows
        )

    def claim_notifications(
        self,
        *,
        worker_id: str,
        limit: int = 20,
        lease_seconds: int = 60,
    ) -> tuple[PendingNotification, ...]:
        with self._pool.connection() as connection, connection.transaction():
            rows = connection.execute(
                """
                WITH candidates AS (
                    SELECT notification_id
                    FROM notification_deliveries
                    WHERE client_type = 'telegram'
                      AND (
                        (
                          state = 'pending'
                          AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                        )
                        OR (state = 'delivering' AND lease_expires_at < now())
                      )
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                ),
                claimed AS (
                    UPDATE notification_deliveries AS delivery
                    SET state = 'delivering',
                        lease_owner = %s,
                        lease_expires_at = now() + (%s * interval '1 second'),
                        updated_at = now()
                    FROM candidates
                    WHERE delivery.notification_id = candidates.notification_id
                    RETURNING delivery.*
                )
                SELECT
                    claimed.notification_id,
                    claimed.recipient_key,
                    subscriber.request_message_id,
                    event.payload
                FROM claimed
                JOIN outbox_events AS event ON event.event_id = claimed.event_id
                JOIN ingestion_subscribers AS subscriber
                  ON subscriber.job_id = event.aggregate_id::uuid
                 AND subscriber.client_type = claimed.client_type
                 AND subscriber.recipient_key = claimed.recipient_key
                ORDER BY claimed.created_at
                """,
                (limit, worker_id, lease_seconds),
            ).fetchall()
        return tuple(
            PendingNotification(
                notification_id=row["notification_id"],
                recipient_key=row["recipient_key"],
                request_message_id=row["request_message_id"],
                message=(f"Saved: {row['payload']['title']}\n{row['payload']['source_url']}"),
            )
            for row in rows
        )

    def mark_notification_delivered(self, notification_id: uuid.UUID) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                UPDATE notification_deliveries
                SET state = 'delivered', delivered_at = now(),
                    attempt_count = attempt_count + 1,
                    lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = now()
                WHERE notification_id = %s
                """,
                (notification_id,),
            )

    def defer_notification(self, notification_id: uuid.UUID) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                UPDATE notification_deliveries
                SET state = 'pending',
                    attempt_count = attempt_count + 1,
                    next_attempt_at = now() + (
                        LEAST(300, 5 * power(2, attempt_count)) * interval '1 second'
                    ),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE notification_id = %s
                """,
                (notification_id,),
            )

    def get_checkpoint(self, key: str) -> int | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT checkpoint_value
                FROM client_checkpoints
                WHERE client_type = 'telegram' AND checkpoint_key = %s
                """,
                (key,),
            ).fetchone()
        return int(row["checkpoint_value"]) if row is not None else None

    def set_checkpoint(self, key: str, value: int) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO client_checkpoints (
                    client_type, checkpoint_key, checkpoint_value
                )
                VALUES ('telegram', %s, %s)
                ON CONFLICT (client_type, checkpoint_key)
                DO UPDATE SET checkpoint_value = EXCLUDED.checkpoint_value, updated_at = now()
                """,
                (key, value),
            )

    def heartbeat(self, *, role: str, instance_id: str) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO service_heartbeats (role, instance_id, last_seen_at)
                VALUES (%s, %s, now())
                ON CONFLICT (role, instance_id)
                DO UPDATE SET last_seen_at = now()
                """,
                (role, instance_id),
            )

    def role_is_healthy(self, role: str, *, maximum_age_seconds: int = 90) -> bool:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM service_heartbeats
                    WHERE role = %s
                      AND last_seen_at >= now() - (%s * interval '1 second')
                ) AS healthy
                """,
                (role, maximum_age_seconds),
            ).fetchone()
        return bool(row["healthy"]) if row is not None else False
