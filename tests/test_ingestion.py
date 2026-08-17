import pytest

from knowledge_assistant.domain.errors import InvalidStateTransitionError
from knowledge_assistant.domain.ingestion import IngestionState


def test_happy_path_reaches_ready() -> None:
    state = IngestionState.ACCEPTED
    for target in (
        IngestionState.QUEUED,
        IngestionState.FETCHING,
        IngestionState.EXTRACTING,
        IngestionState.NORMALIZING,
        IngestionState.VALIDATING,
        IngestionState.COMMITTING,
        IngestionState.INDEXING,
        IngestionState.READY,
    ):
        state = state.transition_to(target)

    assert state is IngestionState.READY
    assert state.terminal


def test_retry_returns_through_queue() -> None:
    state = IngestionState.FETCHING

    state = state.transition_to(IngestionState.RETRY_SCHEDULED)
    state = state.transition_to(IngestionState.QUEUED)

    assert state is IngestionState.QUEUED


def test_invalid_transition_is_rejected() -> None:
    with pytest.raises(InvalidStateTransitionError, match="cannot transition"):
        IngestionState.ACCEPTED.transition_to(IngestionState.READY)
