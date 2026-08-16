"""Optional Prefect flow: idempotent manifest submission and bounded retries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import knowledge_assistant.infrastructure.orchestration.prefect_flow as flow_module
from knowledge_assistant.infrastructure.orchestration.prefect_flow import (
    SUBMIT_RETRIES,
    SUBMIT_RETRY_DELAY_SECONDS,
    ingest_sample_corpus_flow,
)

MANIFEST = Path("data/sample/manifest.json")


class RecordingRepo:
    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []

    def submit(self, **kwargs: object) -> SimpleNamespace:
        self.submissions.append(kwargs)
        created = not str(kwargs["idempotency_key"]).endswith(":sample-pinhole")
        return SimpleNamespace(created=created, state=SimpleNamespace(value="ready"))

    def close(self) -> None:
        return None


class RepoFactory:
    def __init__(self, repo: RecordingRepo) -> None:
        self._repo = repo

    def __call__(self, _database_url: str) -> RecordingRepo:
        return self._repo


class FlakyRepo:
    attempts = 0

    def submit(self, **kwargs: object) -> SimpleNamespace:
        type(self).attempts += 1
        if type(self).attempts == 1:
            raise RuntimeError("transient database failure")
        del kwargs
        return SimpleNamespace(created=True, state=SimpleNamespace(value="ready"))

    def close(self) -> None:
        return None


class FlakyRepoFactory:
    def __call__(self, _database_url: str) -> FlakyRepo:
        return FlakyRepo()


def run_flow() -> dict[str, object]:
    return ingest_sample_corpus_flow(
        manifest_path=str(MANIFEST),
        database_url="postgresql://user:pass@localhost/database",
        recipient=7,
    )


def test_prefect_flow_submits_manifest_sources_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = RecordingRepo()
    monkeypatch.setattr(flow_module, "PostgresIngestionRepository", RepoFactory(repo))

    result = run_flow()

    assert result["dataset_version"] == "sample-docs-v1"
    assert result["sources"] == 4
    assert result["submitted"] == 3
    assert result["already_pending"] == 1
    assert len(repo.submissions) == 4
    assert all(
        str(submission["idempotency_key"]).startswith("sample:")
        for submission in repo.submissions
    )
    assert all(submission["request_message_id"] == "0" for submission in repo.submissions)


def test_prefect_flow_exposes_task_state(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = RecordingRepo()
    monkeypatch.setattr(flow_module, "PostgresIngestionRepository", RepoFactory(repo))

    result = run_flow()

    states = result["task_states"]
    assert isinstance(states, list)
    assert len(states) == 4
    assert all(isinstance(state, dict) for state in states)
    assert all("source_id" in state and "state" in state for state in states)
    assert {state["state"] for state in states} == {"ready"}


def test_prefect_flow_bounded_retries_on_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FlakyRepo.attempts = 0
    monkeypatch.setattr(flow_module, "PostgresIngestionRepository", FlakyRepoFactory())

    result = run_flow()

    assert result["submitted"] == 4
    assert FlakyRepo.attempts == 5  # one transient failure retried, four successes
    assert SUBMIT_RETRIES == 2
    assert SUBMIT_RETRY_DELAY_SECONDS >= 1


def test_prefect_flow_fails_closed_on_missing_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = RecordingRepo()
    monkeypatch.setattr(flow_module, "PostgresIngestionRepository", RepoFactory(repo))

    with pytest.raises((FileNotFoundError, ValueError)):
        ingest_sample_corpus_flow(
            manifest_path="/nonexistent/manifest.json",
            database_url="postgresql://user:pass@localhost/database",
            recipient=7,
        )
