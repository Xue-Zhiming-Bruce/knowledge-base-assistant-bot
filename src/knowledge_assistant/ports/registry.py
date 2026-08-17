"""Operational registry contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from knowledge_assistant.domain.documents import DocumentId, RevisionId
from knowledge_assistant.domain.ingestion import IngestionState


@dataclass(frozen=True, slots=True)
class IngestionJob:
    job_id: str
    idempotency_key: str
    source_url: str
    state: IngestionState
    document_id: DocumentId | None
    revision_id: RevisionId | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime


class IngestionJobRepository(Protocol):
    def get(self, job_id: str) -> IngestionJob | None:
        """Return a job by identity."""

    def get_by_idempotency_key(self, idempotency_key: str) -> IngestionJob | None:
        """Return the existing command outcome for duplicate delivery."""

    def add(self, job: IngestionJob) -> None:
        """Persist a newly accepted job."""

    def transition(
        self,
        job_id: str,
        *,
        expected_state: IngestionState,
        target_state: IngestionState,
    ) -> IngestionJob:
        """Compare-and-set a job state using domain transition rules."""
