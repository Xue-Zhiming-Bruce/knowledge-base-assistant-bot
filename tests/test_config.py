from decimal import Decimal
from pathlib import Path

import pytest

from knowledge_assistant.config import (
    ConfigurationError,
    Environment,
    Settings,
    XArticleProviderName,
)
from knowledge_assistant.domain.retrieval import RetrievalStrategyName


def valid_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "KNOWLEDGE_ASSISTANT_ENVIRONMENT": "test",
        "KNOWLEDGE_ASSISTANT_VAULT_PATH": str(tmp_path / "vault"),
        "KNOWLEDGE_ASSISTANT_DATABASE_URL": (
            "postgresql://knowledge_assistant:secret@localhost/knowledge_assistant"
        ),
        "KNOWLEDGE_ASSISTANT_SESSION_TTL_SECONDS": "900",
    }


def test_settings_load_and_redact_secrets(tmp_path: Path) -> None:
    environment = valid_environment(tmp_path)
    environment["KNOWLEDGE_ASSISTANT_TELEGRAM_TOKEN"] = "should-never-be-returned"
    environment["KNOWLEDGE_ASSISTANT_XQUIK_API_KEY"] = "xq-secret"

    settings = Settings.from_environment(environment)

    assert settings.environment is Environment.TEST
    assert settings.vault_path == (tmp_path / "vault").resolve()
    assert settings.session_ttl_seconds == 900
    assert "should-never-be-returned" not in str(settings.redacted_summary())
    assert "xq-secret" not in str(settings.redacted_summary())
    assert settings.redacted_summary()["xquik_configured"] is True
    assert settings.x_article_provider is XArticleProviderName.XQUIK_MPP
    assert settings.xquik_mpp_max_spend_usdc == Decimal("0.001")
    assert settings.retrieval_strategy is RetrievalStrategyName.WEIGHTED_HYBRID
    assert settings.otel_exporter_otlp_endpoint is None


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("KNOWLEDGE_ASSISTANT_DATABASE_URL", "sqlite:///local.db", "PostgreSQL"),
        ("KNOWLEDGE_ASSISTANT_SESSION_TTL_SECONDS", "not-an-int", "integer"),
        ("KNOWLEDGE_ASSISTANT_SESSION_TTL_SECONDS", "30", "between"),
    ],
)
def test_settings_reject_invalid_values(
    tmp_path: Path,
    key: str,
    value: str,
    message: str,
) -> None:
    environment = valid_environment(tmp_path)
    environment[key] = value

    with pytest.raises(ConfigurationError, match=message):
        Settings.from_environment(environment)


def test_production_requires_telegram_token(tmp_path: Path) -> None:
    environment = valid_environment(tmp_path)
    environment["KNOWLEDGE_ASSISTANT_ENVIRONMENT"] = "production"

    with pytest.raises(ConfigurationError, match="TELEGRAM_TOKEN"):
        Settings.from_environment(environment)


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("KNOWLEDGE_ASSISTANT_VAULT_PATH", "VAULT_PATH"),
        ("KNOWLEDGE_ASSISTANT_DATABASE_URL", "DATABASE_URL"),
    ],
)
def test_settings_require_storage_configuration(
    tmp_path: Path,
    key: str,
    message: str,
) -> None:
    environment = valid_environment(tmp_path)
    del environment[key]

    with pytest.raises(ConfigurationError, match=message):
        Settings.from_environment(environment)


def test_settings_reject_unknown_environment(tmp_path: Path) -> None:
    environment = valid_environment(tmp_path)
    environment["KNOWLEDGE_ASSISTANT_ENVIRONMENT"] = "somewhere"

    with pytest.raises(ConfigurationError, match="must be one of"):
        Settings.from_environment(environment)


def test_service_configuration_and_requirements(tmp_path: Path) -> None:
    environment = valid_environment(tmp_path)
    environment.update(
        {
            "KNOWLEDGE_ASSISTANT_TELEGRAM_TOKEN": "token",
            "KNOWLEDGE_ASSISTANT_TELEGRAM_ALLOWED_USER_IDS": "12, 34",
            "KNOWLEDGE_ASSISTANT_TELEGRAM_POLL_TIMEOUT_SECONDS": "10",
            "KNOWLEDGE_ASSISTANT_WORKER_POLL_SECONDS": "0.5",
            "OPENAI_API_KEY": "key",
            "KNOWLEDGE_ASSISTANT_EMBEDDING_MODEL": "embedding",
        }
    )

    settings = Settings.from_environment(environment)
    settings.require_bot()
    settings.require_worker()

    assert settings.telegram_allowed_user_ids == frozenset({12, 34})
    assert settings.telegram_poll_timeout_seconds == 10
    assert settings.worker_poll_seconds == 0.5


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("KNOWLEDGE_ASSISTANT_TELEGRAM_ALLOWED_USER_IDS", "abc", "comma-separated"),
        ("KNOWLEDGE_ASSISTANT_TELEGRAM_ALLOWED_USER_IDS", "-1", "positive"),
        ("KNOWLEDGE_ASSISTANT_TELEGRAM_POLL_TIMEOUT_SECONDS", "bad", "integer"),
        ("KNOWLEDGE_ASSISTANT_TELEGRAM_POLL_TIMEOUT_SECONDS", "90", "between"),
        ("KNOWLEDGE_ASSISTANT_WORKER_POLL_SECONDS", "bad", "number"),
        ("KNOWLEDGE_ASSISTANT_WORKER_POLL_SECONDS", "0", "between"),
        ("KNOWLEDGE_ASSISTANT_XQUIK_MPP_MAX_SPEND_USDC", "bad", "decimal"),
        ("KNOWLEDGE_ASSISTANT_XQUIK_MPP_MAX_SPEND_USDC", "0", "between"),
        ("KNOWLEDGE_ASSISTANT_XQUIK_MPP_MAX_SPEND_USDC", "1.1", "between"),
        ("KNOWLEDGE_ASSISTANT_RETRIEVAL_STRATEGY", "unknown", "must be one of"),
        ("KNOWLEDGE_ASSISTANT_OTEL_EXPORTER_OTLP_ENDPOINT", "file:///tmp/x", "HTTP"),
    ],
)
def test_service_configuration_rejects_invalid_values(
    tmp_path: Path,
    key: str,
    value: str,
    message: str,
) -> None:
    environment = valid_environment(tmp_path)
    environment[key] = value

    with pytest.raises(ConfigurationError, match=message):
        Settings.from_environment(environment)


def test_service_requirements_fail_closed(tmp_path: Path) -> None:
    settings = Settings.from_environment(valid_environment(tmp_path))

    with pytest.raises(ConfigurationError, match="TELEGRAM_TOKEN"):
        settings.require_bot()
    with pytest.raises(ConfigurationError, match="completion notifications"):
        settings.require_worker()

    environment = valid_environment(tmp_path)
    environment["KNOWLEDGE_ASSISTANT_TELEGRAM_TOKEN"] = "token"
    settings = Settings.from_environment(environment)
    with pytest.raises(ConfigurationError, match="ALLOWED_USER_IDS"):
        settings.require_bot()
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        settings.require_worker()

    environment["OPENAI_API_KEY"] = "key"
    settings = Settings.from_environment(environment)
    with pytest.raises(ConfigurationError, match="EMBEDDING_MODEL"):
        settings.require_worker()


def test_xquik_provider_requires_worker_only_secret(tmp_path: Path) -> None:
    environment = valid_environment(tmp_path)
    environment.update(
        {
            "KNOWLEDGE_ASSISTANT_X_ARTICLE_PROVIDER": "xquik",
            "KNOWLEDGE_ASSISTANT_TELEGRAM_TOKEN": "token",
            "OPENAI_API_KEY": "key",
            "KNOWLEDGE_ASSISTANT_EMBEDDING_MODEL": "embedding",
        }
    )
    settings = Settings.from_environment(environment)

    assert settings.x_article_provider is XArticleProviderName.XQUIK
    with pytest.raises(ConfigurationError, match="XQUIK_API_KEY"):
        settings.require_worker()

    environment["KNOWLEDGE_ASSISTANT_XQUIK_API_KEY"] = "xq-secret"
    Settings.from_environment(environment).require_worker()

    environment["KNOWLEDGE_ASSISTANT_X_ARTICLE_PROVIDER"] = "xquik_mpp"
    del environment["KNOWLEDGE_ASSISTANT_XQUIK_API_KEY"]
    mpp_settings = Settings.from_environment(environment)
    mpp_settings.require_worker()
    assert mpp_settings.x_article_provider is XArticleProviderName.XQUIK_MPP


def test_settings_reject_unknown_x_article_provider(tmp_path: Path) -> None:
    environment = valid_environment(tmp_path)
    environment["KNOWLEDGE_ASSISTANT_X_ARTICLE_PROVIDER"] = "browser-cookie"

    with pytest.raises(ConfigurationError, match="xquik, xquik_mpp"):
        Settings.from_environment(environment)


def test_settings_load_agentic_strategy_and_telemetry(tmp_path: Path) -> None:
    environment = valid_environment(tmp_path)
    environment.update(
        {
            "KNOWLEDGE_ASSISTANT_RETRIEVAL_STRATEGY": "agentic-decomposition-v1",
            "KNOWLEDGE_ASSISTANT_OTEL_EXPORTER_OTLP_ENDPOINT": "http://lgtm:4318",
        }
    )

    settings = Settings.from_environment(environment)

    assert settings.retrieval_strategy is RetrievalStrategyName.AGENTIC_DECOMPOSITION
    assert settings.otel_exporter_otlp_endpoint == "http://lgtm:4318"
