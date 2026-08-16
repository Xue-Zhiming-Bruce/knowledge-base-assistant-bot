"""Optional Prefect orchestration for the public sample corpus.

The flow is intentionally thin: it classifies each manifest URL and submits an
idempotent ingestion job through the normal application contract. It does not
re-implement fetching, extraction, vault writes, chunking, or projection writes -
the existing worker performs those stages exactly as it does for Telegram
submissions, preserving the canonical-vault and rebuildable-projection contracts.

Prefect is an optional dependency (the ``orchestration`` extra). The normal bot
and worker runtime never imports this module, so the base image stays minimal.
"""

from __future__ import annotations

from pathlib import Path

from prefect import flow, task

from knowledge_assistant.application.evaluation import load_sample_manifest
from knowledge_assistant.domain.sources import SourceClassifier
from knowledge_assistant.infrastructure.postgres.ingestion_repository import (
    PostgresIngestionRepository,
)

SUBMIT_RETRIES = 2
SUBMIT_RETRY_DELAY_SECONDS = 2


@task(
    name="classify-and-submit-sample-source",
    retries=SUBMIT_RETRIES,
    retry_delay_seconds=SUBMIT_RETRY_DELAY_SECONDS,
)
def submit_sample_source(
    source_id: str, url: str, recipient: int, database_url: str
) -> dict[str, object]:
    """Classify one manifest URL and submit an idempotent ingestion job.

    Retries are bounded (``SUBMIT_RETRIES``) and cover transient database and
    classification failures. An already-pending job is not an error: the
    idempotency key ``sample:<source_id>`` makes resubmission a no-op.
    """

    classifier = SourceClassifier()
    repository = PostgresIngestionRepository(database_url)
    try:
        classified = classifier.classify(url)
        submission = repository.submit(
            idempotency_key=f"sample:{source_id}",
            source=classified,
            recipient_key=str(recipient),
            request_message_id="0",
        )
        return {
            "source_id": source_id,
            "url": url,
            "created": submission.created,
            "state": submission.state.value,
        }
    finally:
        repository.close()


@flow(name="ingest-sample-corpus")
def ingest_sample_corpus_flow(
    manifest_path: str,
    database_url: str,
    recipient: int = 0,
) -> dict[str, object]:
    """Submit every source in the public sample manifest to the ingestion queue."""

    manifest = load_sample_manifest(Path(manifest_path))
    results = [
        submit_sample_source(source.source_id, source.url, recipient, database_url)
        for source in manifest.sources
    ]
    return {
        "dataset_version": manifest.dataset_version,
        "sources": len(results),
        "submitted": sum(1 for result in results if result["created"]),
        "already_pending": sum(1 for result in results if not result["created"]),
        "task_states": results,
    }
