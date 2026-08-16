from pathlib import Path

import pytest

from knowledge_assistant.cli import build_parser, main


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
