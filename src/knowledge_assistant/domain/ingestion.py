"""Ingestion workflow state and transition policy."""

from __future__ import annotations

from enum import StrEnum

from knowledge_assistant.domain.errors import InvalidStateTransitionError


class IngestionState(StrEnum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    FETCHING = "fetching"
    EXTRACTING = "extracting"
    NORMALIZING = "normalizing"
    VALIDATING = "validating"
    COMMITTING = "committing"
    INDEXING = "indexing"
    READY = "ready"
    READY_DEGRADED = "ready_degraded"
    RETRY_SCHEDULED = "retry_scheduled"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    CONFLICT = "conflict"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {
            IngestionState.READY,
            IngestionState.REJECTED,
            IngestionState.FAILED,
        }

    def transition_to(self, target: IngestionState) -> IngestionState:
        if target not in _ALLOWED_TRANSITIONS[self]:
            raise InvalidStateTransitionError(
                f"cannot transition ingestion from {self} to {target}"
            )
        return target


_ALLOWED_TRANSITIONS: dict[IngestionState, frozenset[IngestionState]] = {
    IngestionState.ACCEPTED: frozenset({IngestionState.QUEUED, IngestionState.REJECTED}),
    IngestionState.QUEUED: frozenset({IngestionState.FETCHING}),
    IngestionState.FETCHING: frozenset(
        {
            IngestionState.EXTRACTING,
            IngestionState.RETRY_SCHEDULED,
            IngestionState.FAILED,
        }
    ),
    IngestionState.EXTRACTING: frozenset(
        {
            IngestionState.NORMALIZING,
            IngestionState.RETRY_SCHEDULED,
            IngestionState.FAILED,
        }
    ),
    IngestionState.NORMALIZING: frozenset(
        {
            IngestionState.VALIDATING,
            IngestionState.RETRY_SCHEDULED,
            IngestionState.FAILED,
        }
    ),
    IngestionState.VALIDATING: frozenset(
        {
            IngestionState.COMMITTING,
            IngestionState.NEEDS_REVIEW,
            IngestionState.REJECTED,
            IngestionState.FAILED,
        }
    ),
    IngestionState.COMMITTING: frozenset(
        {IngestionState.INDEXING, IngestionState.CONFLICT, IngestionState.FAILED}
    ),
    IngestionState.INDEXING: frozenset(
        {
            IngestionState.READY,
            IngestionState.READY_DEGRADED,
            IngestionState.RETRY_SCHEDULED,
            IngestionState.FAILED,
        }
    ),
    IngestionState.RETRY_SCHEDULED: frozenset({IngestionState.QUEUED}),
    IngestionState.READY_DEGRADED: frozenset({IngestionState.INDEXING}),
    IngestionState.NEEDS_REVIEW: frozenset({IngestionState.QUEUED, IngestionState.REJECTED}),
    IngestionState.CONFLICT: frozenset({IngestionState.QUEUED, IngestionState.REJECTED}),
    IngestionState.READY: frozenset(),
    IngestionState.REJECTED: frozenset(),
    IngestionState.FAILED: frozenset(),
}
