import uuid
from pathlib import Path

import pytest

from knowledge_assistant.application.deletion import ArticleDeletionResult
from knowledge_assistant.cli import build_parser, main
from knowledge_assistant.domain.ingestion import IngestionState


def configure_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KNOWLEDGE_ASSISTANT_ENVIRONMENT", "test")
    monkeypatch.setenv("KNOWLEDGE_ASSISTANT_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv(
        "KNOWLEDGE_ASSISTANT_DATABASE_URL",
        "postgresql://knowledge_assistant:secret@localhost/knowledge_assistant",
    )


def test_check_config_prints_redacted_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("KNOWLEDGE_ASSISTANT_TELEGRAM_TOKEN", "must-not-appear")

    result = main(["check-config"])

    output = capsys.readouterr().out
    assert result == 0
    assert '"environment": "test"' in output
    assert "must-not-appear" not in output


def test_check_config_reports_invalid_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KNOWLEDGE_ASSISTANT_VAULT_PATH", raising=False)

    result = main(["check-config"])

    assert result == 2
    assert "configuration error" in capsys.readouterr().out


def test_migrate_applies_database_migrations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_environment(monkeypatch, tmp_path)

    class FakeMigrationRunner:
        def __init__(self, database_url: str) -> None:
            assert database_url.startswith("postgresql://")

        def apply(self) -> tuple[str, ...]:
            return ("0001_initial",)

    monkeypatch.setattr(
        "knowledge_assistant.cli.MigrationRunner",
        FakeMigrationRunner,
    )

    assert main(["migrate"]) == 0
    assert "0001_initial" in capsys.readouterr().out


def test_migrate_reports_up_to_date(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_environment(monkeypatch, tmp_path)

    class FakeMigrationRunner:
        def __init__(self, _database_url: str) -> None:
            pass

        def apply(self) -> tuple[str, ...]:
            return ()

    monkeypatch.setattr(
        "knowledge_assistant.cli.MigrationRunner",
        FakeMigrationRunner,
    )

    assert main(["migrate"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_parser_exposes_projection_and_evaluation_commands(tmp_path: Path) -> None:
    parser = build_parser()

    projection = parser.parse_args(["projection-rebuild", "--activate"])
    activation = parser.parse_args(["projection-activate", "00000000-0000-0000-0000-000000000001"])
    generation = parser.parse_args(
        ["eval-generate", "--output", str(tmp_path / "dataset.jsonl"), "--count", "5"]
    )
    evaluation = parser.parse_args(
        [
            "eval-run",
            "--dataset",
            str(tmp_path / "dataset.jsonl"),
            "--output",
            str(tmp_path / "results.jsonl"),
            "--strategy",
            "rrf-hybrid-v1",
        ]
    )

    assert projection.activate
    assert str(activation.generation_id) == "00000000-0000-0000-0000-000000000001"
    assert generation.count == 5
    assert evaluation.strategy == "rrf-hybrid-v1"


def test_ingest_classifies_and_submits_without_a_telegram_subscriber(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_environment(monkeypatch, tmp_path)
    # Ambient .env from other test modules may carry an allowlist; headless means none.
    monkeypatch.delenv("KNOWLEDGE_ASSISTANT_TELEGRAM_ALLOWED_USER_IDS", raising=False)
    submitted: list[dict[str, object]] = []

    class FakeJobSubmission:
        job_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        state = IngestionState.QUEUED
        created = True

    class FakeRepository:
        def __init__(self, database_url: str) -> None:
            assert database_url.startswith("postgresql://")

        def submit(self, **kwargs: object) -> FakeJobSubmission:
            submitted.append(kwargs)
            return FakeJobSubmission()

        def close(self) -> None:
            pass

    monkeypatch.setattr("knowledge_assistant.cli.PostgresIngestionRepository", FakeRepository)

    assert main(["ingest", "https://example.com/an-article"]) == 0

    assert len(submitted) == 1
    assert submitted[0]["recipient_key"] is None
    assert submitted[0]["request_message_id"] is None
    # Stable per source URL: a retried fetch is the same idempotency key.
    assert str(submitted[0]["idempotency_key"]).startswith("cli:web:sha256:")
    output = capsys.readouterr().out
    assert '"state": "queued"' in output
    assert '"created": true' in output


def test_ingest_rejects_a_non_url_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_environment(monkeypatch, tmp_path)

    class ExplodingRepository:
        def __init__(self, _database_url: str) -> None:
            raise AssertionError("the database must not be reached for a rejected URL")

    monkeypatch.setattr("knowledge_assistant.cli.PostgresIngestionRepository", ExplodingRepository)

    assert main(["ingest", "not-a-url"]) == 2
    assert '"status": "rejected"' in capsys.readouterr().out


def test_delete_reports_a_deleted_article(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_environment(monkeypatch, tmp_path)

    class FakeDeletionService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def delete_by_title(self, title: str) -> ArticleDeletionResult:
            return ArticleDeletionResult(deleted_title=title)

    class FakeRepository:
        def __init__(self, _database_url: str) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr("knowledge_assistant.cli.PostgresIngestionRepository", FakeRepository)
    monkeypatch.setattr("knowledge_assistant.cli.ArticleDeletionService", FakeDeletionService)

    assert main(["delete", "21 Lessons from 14 Years at Google"]) == 0
    assert '"status": "deleted"' in capsys.readouterr().out


def test_delete_deletes_nothing_on_an_ambiguous_title(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_environment(monkeypatch, tmp_path)

    class FakeDeletionService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def delete_by_title(self, _title: str) -> ArticleDeletionResult:
            return ArticleDeletionResult(suggestions=("Lesson One", "Lesson Two"))

    class FakeRepository:
        def __init__(self, _database_url: str) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr("knowledge_assistant.cli.PostgresIngestionRepository", FakeRepository)
    monkeypatch.setattr("knowledge_assistant.cli.ArticleDeletionService", FakeDeletionService)

    assert main(["delete", "Lesson"]) == 1
    output = capsys.readouterr().out
    assert '"status": "not_deleted"' in output
    assert "Lesson One" in output
