from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_compose() -> dict[str, Any]:
    payload = yaml.safe_load((PROJECT_ROOT / "compose.yaml").read_text())
    assert isinstance(payload, dict)
    return payload


def test_docker_build_is_locked_and_runs_as_non_root() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()

    assert "python:${PYTHON_VERSION}-slim-trixie" in dockerfile
    assert "ghcr.io/astral-sh/uv:${UV_VERSION}" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
    assert "USER knowledge-assistant" in dockerfile
    assert 'ENTRYPOINT ["knowledge-assistant"]' in dockerfile
    assert "COVERAGE_FILE=/tmp/.coverage" in dockerfile
    assert "COPY .env" not in dockerfile
    assert "TEMPO_VERSION=v1.11.0" in dockerfile
    assert "TEMPO_WALLET_VERSION=0.6.7" in dockerfile
    assert "TEMPO_REQUEST_VERSION=0.6.7" in dockerfile
    assert "/usr/local/bin/tempo-request" in dockerfile


def test_dockerignore_excludes_secrets_and_local_state() -> None:
    ignored = set((PROJECT_ROOT / ".dockerignore").read_text().splitlines())

    for required_pattern in (
        ".env",
        ".env.*",
        ".venv",
        "knowledge-assistant.venv",
        "var",
        ".git",
    ):
        assert required_pattern in ignored


def test_postgres_is_pinned_persistent_and_host_local() -> None:
    compose = load_compose()
    postgres = compose["services"]["postgres"]

    assert postgres["image"] == "pgvector/pgvector:0.8.2-pg17-bookworm"
    assert postgres["ports"] == ["127.0.0.1:${KNOWLEDGE_ASSISTANT_POSTGRES_PORT:-5432}:5432"]
    assert "postgres-data:/var/lib/postgresql/data" in postgres["volumes"]
    assert "pg_isready" in " ".join(postgres["healthcheck"]["test"])


def test_migration_waits_for_database_health() -> None:
    compose = load_compose()
    migration = compose["services"]["migrate"]

    assert migration["command"] == ["migrate"]
    assert migration["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert migration["read_only"] is True
    assert "no-new-privileges:true" in migration["security_opt"]


def test_vault_is_runtime_mount_not_image_content() -> None:
    compose = load_compose()
    admin = compose["services"]["admin"]
    mount = admin["volumes"][0]

    assert mount["type"] == "bind"
    assert mount["target"] == "/data/vault"
    assert compose["x-app-environment"]["KNOWLEDGE_ASSISTANT_VAULT_PATH"] == "/data/vault"

    for service in ("bot", "worker", "admin"):
        assert mount in compose["services"][service]["volumes"]


def test_admin_has_private_writable_evaluation_output() -> None:
    compose = load_compose()

    assert {
        "type": "bind",
        "source": "./var/evaluation",
        "target": "/data/evaluation",
    } in compose["services"]["admin"]["volumes"]


def test_runtime_does_not_mount_a_model_cache() -> None:
    compose = load_compose()

    for service in ("bot", "worker", "admin"):
        assert all(
            mount.get("target") != "/home/knowledge-assistant/.cache/huggingface"
            for mount in compose["services"][service]["volumes"]
        )
    assert "huggingface-cache" not in compose["volumes"]


def test_xquik_key_is_scoped_to_worker_only() -> None:
    compose = load_compose()
    common_environment = compose["x-app-environment"]
    worker_environment = compose["services"]["worker"]["environment"]

    assert "KNOWLEDGE_ASSISTANT_XQUIK_API_KEY" not in common_environment
    assert "KNOWLEDGE_ASSISTANT_X_BEARER_TOKEN" not in common_environment
    assert "KNOWLEDGE_ASSISTANT_X_AUTH_TOKEN" not in common_environment
    assert "KNOWLEDGE_ASSISTANT_X_CT0" not in common_environment
    assert "KNOWLEDGE_ASSISTANT_XQUIK_API_KEY" in worker_environment


def test_tempo_wallet_is_scoped_to_worker_and_auth_tool() -> None:
    compose = load_compose()
    worker = compose["services"]["worker"]
    auth = compose["services"]["tempo-auth"]

    worker_volumes = worker["volumes"]
    assert {
        "type": "volume",
        "source": "tempo-wallet",
        "target": "/home/knowledge-assistant/.tempo",
    } in worker_volumes
    assert auth["entrypoint"] == ["/usr/local/bin/tempo-wallet"]
    assert auth["command"] == ["login", "--no-browser"]
    assert auth["profiles"] == ["tools"]
    assert auth["volumes"] == [
        {
            "type": "volume",
            "source": "tempo-wallet",
            "target": "/home/knowledge-assistant/.tempo",
        }
    ]
    assert "tempo-wallet" in compose["volumes"]


def test_monitoring_profile_is_pinned_local_and_persistent() -> None:
    compose = load_compose()
    lgtm = compose["services"]["lgtm"]

    assert lgtm["image"] == "grafana/otel-lgtm:0.30.0"
    assert lgtm["profiles"] == ["monitoring"]
    assert lgtm["ports"] == [
        "127.0.0.1:${KNOWLEDGE_ASSISTANT_GRAFANA_PORT:-3000}:3000",
        "127.0.0.1:${KNOWLEDGE_ASSISTANT_OTLP_HTTP_PORT:-4318}:4318",
    ]
    assert "lgtm-data:/data" in lgtm["volumes"]
    assert "lgtm-data" in compose["volumes"]
