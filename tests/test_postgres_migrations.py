from __future__ import annotations

import re
from collections.abc import Sequence
from types import TracebackType
from typing import Any

import pytest

from knowledge_assistant.infrastructure.postgres.migrations import (
    MigrationError,
    MigrationRunner,
)


class FakeResult:
    def __init__(self, rows: Sequence[tuple[str, str]]) -> None:
        self._rows = rows

    def fetchall(self) -> Sequence[tuple[str, str]]:
        return self._rows


class FakeConnection:
    def __init__(self, applied: Sequence[tuple[str, str]] = ()) -> None:
        self.applied = applied
        self.statements: list[str] = []

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def execute(self, query: Any, params: object = None) -> FakeResult:
        statement = str(query)
        self.statements.append(statement)
        if "SELECT version, checksum" in statement:
            return FakeResult(self.applied)
        return FakeResult(())


def test_initial_migration_contains_operational_and_rag_schema() -> None:
    migrations = MigrationRunner._load_migrations()

    assert [migration.version for migration in migrations] == [
        "0001_initial",
        "0002_ingestion_runtime",
        "0003_notification_leases",
        "0004_document_assets",
        "0005_learned_sparse_embeddings",
        "0006_remove_learned_sparse_embeddings",
        "0007_answer_feedback",
        "0008_feedback_durable",
    ]
    sql = migrations[0].sql
    for required_fragment in (
        "CREATE EXTENSION IF NOT EXISTS vector",
        "CREATE TABLE documents",
        "CREATE TABLE ingestion_jobs",
        "CREATE TABLE question_sessions",
        "CREATE TABLE projection_generations",
        "CREATE TABLE chunks",
        "embedding vector",
        "search_vector tsvector",
    ):
        assert required_fragment in sql
    assert re.fullmatch(r"[a-f0-9]{64}", migrations[0].checksum)
    assert "ADD COLUMN sparse_embedding sparsevec" in migrations[4].sql
    assert "DROP COLUMN IF EXISTS sparse_embedding" in migrations[5].sql
    # 0008 must be forward-only: it decouples feedback from temporary sessions by
    # dropping the cascading foreign keys (feedback survives /end and expiry).
    assert "DROP CONSTRAINT answer_feedback_session_id_fkey" in migrations[7].sql
    assert "DROP CONSTRAINT answer_feedback_turn_fk" in migrations[7].sql
    assert "CREATE TABLE" not in migrations[7].sql
    assert "DROP TABLE" not in migrations[7].sql


def test_migration_runner_applies_pending_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.postgres.migrations.psycopg.connect",
        lambda _url: connection,
    )

    applied = MigrationRunner("postgresql+psycopg://user:password@localhost/database").apply()

    assert applied == (
        "0001_initial",
        "0002_ingestion_runtime",
        "0003_notification_leases",
        "0004_document_assets",
        "0005_learned_sparse_embeddings",
        "0006_remove_learned_sparse_embeddings",
        "0007_answer_feedback",
        "0008_feedback_durable",
    )
    assert any("pg_advisory_xact_lock" in statement for statement in connection.statements)
    assert any("CREATE TABLE documents" in statement for statement in connection.statements)


def test_migration_runner_rejects_changed_applied_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection((("0001_initial", "wrong-checksum"),))
    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.postgres.migrations.psycopg.connect",
        lambda _url: connection,
    )

    with pytest.raises(MigrationError, match="changed"):
        MigrationRunner("postgresql://localhost/database").apply()
