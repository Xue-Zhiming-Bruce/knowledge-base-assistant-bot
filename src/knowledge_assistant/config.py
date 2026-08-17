"""Validated runtime configuration loaded from the process environment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from knowledge_assistant.domain.retrieval import RetrievalStrategyName


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class XArticleProviderName(StrEnum):
    XQUIK = "xquik"
    XQUIK_MPP = "xquik_mpp"


class AnswerPromptVersion(StrEnum):
    """Validated grounded-answer prompt versions.

    ``grounded-answer-v2`` is the evaluated production default; v1 exists only
    as an explicit baseline override.
    """

    GROUNDED_ANSWER_V1 = "grounded-answer-v1"
    GROUNDED_ANSWER_V2 = "grounded-answer-v2"


class ConfigurationError(ValueError):
    """Raised when runtime configuration is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class Settings:
    environment: Environment
    vault_path: Path
    database_url: str
    telegram_token: str | None
    telegram_allowed_user_ids: frozenset[int]
    telegram_poll_timeout_seconds: int
    worker_poll_seconds: float
    openai_api_key: str | None
    generation_model: str | None
    embedding_model: str | None
    x_article_provider: XArticleProviderName
    xquik_api_key: str | None
    xquik_mpp_max_spend_usdc: Decimal
    session_ttl_seconds: int
    retrieval_strategy: RetrievalStrategyName
    answer_prompt_version: AnswerPromptVersion
    otel_exporter_otlp_endpoint: str | None

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> Settings:
        prefix = "KNOWLEDGE_ASSISTANT_"

        try:
            environment = Environment(environ.get(f"{prefix}ENVIRONMENT", "development"))
        except ValueError as error:
            choices = ", ".join(item.value for item in Environment)
            raise ConfigurationError(f"ENVIRONMENT must be one of: {choices}") from error

        vault_value = environ.get(f"{prefix}VAULT_PATH")
        if not vault_value:
            raise ConfigurationError(f"{prefix}VAULT_PATH is required")
        vault_path = Path(vault_value).expanduser().resolve()

        database_url = environ.get(f"{prefix}DATABASE_URL", "")
        if not database_url:
            raise ConfigurationError(f"{prefix}DATABASE_URL is required")
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ConfigurationError("DATABASE_URL must use PostgreSQL")

        ttl_raw = environ.get(f"{prefix}SESSION_TTL_SECONDS", "3600")
        try:
            session_ttl_seconds = int(ttl_raw)
        except ValueError as error:
            raise ConfigurationError("SESSION_TTL_SECONDS must be an integer") from error
        if not 60 <= session_ttl_seconds <= 86_400:
            raise ConfigurationError("SESSION_TTL_SECONDS must be between 60 and 86400")

        telegram_token = environ.get(f"{prefix}TELEGRAM_TOKEN") or None
        if environment is Environment.PRODUCTION and telegram_token is None:
            raise ConfigurationError("TELEGRAM_TOKEN is required in production")

        allowed_user_ids = cls._parse_user_ids(
            environ.get(f"{prefix}TELEGRAM_ALLOWED_USER_IDS", "")
        )
        telegram_poll_timeout_seconds = cls._parse_int(
            environ.get(f"{prefix}TELEGRAM_POLL_TIMEOUT_SECONDS", "25"),
            "TELEGRAM_POLL_TIMEOUT_SECONDS",
            minimum=1,
            maximum=50,
        )
        worker_poll_seconds = cls._parse_float(
            environ.get(f"{prefix}WORKER_POLL_SECONDS", "2"),
            "WORKER_POLL_SECONDS",
            minimum=0.1,
            maximum=60,
        )
        try:
            x_article_provider = XArticleProviderName(
                environ.get(f"{prefix}X_ARTICLE_PROVIDER", "xquik_mpp")
            )
        except ValueError as error:
            raise ConfigurationError(
                "X_ARTICLE_PROVIDER must be one of: xquik, xquik_mpp"
            ) from error
        xquik_mpp_max_spend_usdc = cls._parse_decimal(
            environ.get(f"{prefix}XQUIK_MPP_MAX_SPEND_USDC", "0.001"),
            "XQUIK_MPP_MAX_SPEND_USDC",
            minimum=Decimal("0.000001"),
            maximum=Decimal("1"),
        )
        try:
            retrieval_strategy = RetrievalStrategyName(
                environ.get(
                    f"{prefix}RETRIEVAL_STRATEGY",
                    RetrievalStrategyName.WEIGHTED_HYBRID.value,
                )
            )
        except ValueError as error:
            choices = ", ".join(item.value for item in RetrievalStrategyName)
            raise ConfigurationError(f"RETRIEVAL_STRATEGY must be one of: {choices}") from error
        try:
            answer_prompt_version = AnswerPromptVersion(
                environ.get(
                    f"{prefix}ANSWER_PROMPT_VERSION",
                    AnswerPromptVersion.GROUNDED_ANSWER_V2.value,
                )
            )
        except ValueError as error:
            choices = ", ".join(item.value for item in AnswerPromptVersion)
            raise ConfigurationError(f"ANSWER_PROMPT_VERSION must be one of: {choices}") from error
        otel_endpoint = environ.get(f"{prefix}OTEL_EXPORTER_OTLP_ENDPOINT") or None
        if otel_endpoint is not None:
            parsed_endpoint = urlsplit(otel_endpoint)
            if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.hostname:
                raise ConfigurationError("OTEL_EXPORTER_OTLP_ENDPOINT must be an HTTP(S) URL")

        return cls(
            environment=environment,
            vault_path=vault_path,
            database_url=database_url,
            telegram_token=telegram_token,
            telegram_allowed_user_ids=allowed_user_ids,
            telegram_poll_timeout_seconds=telegram_poll_timeout_seconds,
            worker_poll_seconds=worker_poll_seconds,
            openai_api_key=environ.get("OPENAI_API_KEY") or None,
            generation_model=environ.get(f"{prefix}GENERATION_MODEL") or None,
            embedding_model=environ.get(f"{prefix}EMBEDDING_MODEL") or None,
            x_article_provider=x_article_provider,
            xquik_api_key=environ.get(f"{prefix}XQUIK_API_KEY") or None,
            xquik_mpp_max_spend_usdc=xquik_mpp_max_spend_usdc,
            session_ttl_seconds=session_ttl_seconds,
            retrieval_strategy=retrieval_strategy,
            answer_prompt_version=answer_prompt_version,
            otel_exporter_otlp_endpoint=otel_endpoint,
        )

    def redacted_summary(self) -> dict[str, str | int | bool]:
        """Return settings safe for diagnostics and startup logs."""

        return {
            "environment": self.environment.value,
            "vault_path": str(self.vault_path),
            "database_configured": bool(self.database_url),
            "telegram_configured": self.telegram_token is not None,
            "telegram_allowlist_configured": bool(self.telegram_allowed_user_ids),
            "openai_configured": self.openai_api_key is not None,
            "generation_model_configured": self.generation_model is not None,
            "embedding_model_configured": self.embedding_model is not None,
            "x_article_provider": self.x_article_provider.value,
            "xquik_configured": self.xquik_api_key is not None,
            "xquik_mpp_max_spend_usdc": str(self.xquik_mpp_max_spend_usdc),
            "session_ttl_seconds": self.session_ttl_seconds,
            "retrieval_strategy": self.retrieval_strategy.value,
            "answer_prompt_version": self.answer_prompt_version.value,
            "telemetry_configured": self.otel_exporter_otlp_endpoint is not None,
        }

    def require_bot(self) -> None:
        if self.telegram_token is None:
            raise ConfigurationError("TELEGRAM_TOKEN is required for the bot service")
        if not self.telegram_allowed_user_ids:
            raise ConfigurationError(
                "TELEGRAM_ALLOWED_USER_IDS must contain at least one numeric user ID"
            )

    def require_worker(self) -> None:
        if self.openai_api_key is None:
            raise ConfigurationError("OPENAI_API_KEY is required for the worker service")
        if self.embedding_model is None:
            raise ConfigurationError("EMBEDDING_MODEL is required for the worker service")
        if self.x_article_provider is XArticleProviderName.XQUIK and self.xquik_api_key is None:
            raise ConfigurationError("XQUIK_API_KEY is required when X_ARTICLE_PROVIDER=xquik")

    def require_question_service(self) -> None:
        if self.openai_api_key is None:
            raise ConfigurationError("OPENAI_API_KEY is required for Question Mode")
        if self.embedding_model is None:
            raise ConfigurationError("EMBEDDING_MODEL is required for Question Mode")
        if self.generation_model is None:
            raise ConfigurationError("GENERATION_MODEL is required for Question Mode")

    def require_projection_service(self) -> None:
        if self.openai_api_key is None:
            raise ConfigurationError("OPENAI_API_KEY is required for projection rebuilds")
        if self.embedding_model is None:
            raise ConfigurationError("EMBEDDING_MODEL is required for projection rebuilds")

    @staticmethod
    def _parse_user_ids(raw: str) -> frozenset[int]:
        if not raw.strip():
            return frozenset()
        try:
            identifiers = frozenset(int(item.strip()) for item in raw.split(","))
        except ValueError as error:
            raise ConfigurationError(
                "TELEGRAM_ALLOWED_USER_IDS must be comma-separated integers"
            ) from error
        if any(identifier <= 0 for identifier in identifiers):
            raise ConfigurationError("TELEGRAM_ALLOWED_USER_IDS must contain positive integers")
        return identifiers

    @staticmethod
    def _parse_int(
        raw: str,
        name: str,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            value = int(raw)
        except ValueError as error:
            raise ConfigurationError(f"{name} must be an integer") from error
        if not minimum <= value <= maximum:
            raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
        return value

    @staticmethod
    def _parse_float(
        raw: str,
        name: str,
        *,
        minimum: float,
        maximum: float,
    ) -> float:
        try:
            value = float(raw)
        except ValueError as error:
            raise ConfigurationError(f"{name} must be a number") from error
        if not minimum <= value <= maximum:
            raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
        return value

    @staticmethod
    def _parse_decimal(
        raw: str,
        name: str,
        *,
        minimum: Decimal,
        maximum: Decimal,
    ) -> Decimal:
        try:
            value = Decimal(raw)
        except InvalidOperation as error:
            raise ConfigurationError(f"{name} must be a decimal number") from error
        if not value.is_finite() or not minimum <= value <= maximum:
            raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
        return value
