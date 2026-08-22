"""Administrative command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
from dotenv import load_dotenv

from knowledge_assistant.application.assets import ArticleAssetMaterializer
from knowledge_assistant.application.bot import TelegramPollingService
from knowledge_assistant.application.deletion import ArticleDeletionService
from knowledge_assistant.application.evaluation import (
    _JUDGE_DIMENSIONS,
    AnswerEvaluationRunner,
    RetrievalEvaluationRunner,
    SyntheticDatasetBuilder,
    calibrate_judge_scores,
    load_dataset,
    load_human_labels,
    load_sample_manifest,
    render_answer_evaluation_markdown,
    render_calibration_markdown,
    sample_cases_to_dataset,
    write_jsonl,
)
from knowledge_assistant.application.projections import ProjectionRebuildService
from knowledge_assistant.application.questions import QuestionService
from knowledge_assistant.application.retrieval import RetrievalOrchestrator
from knowledge_assistant.application.worker import IngestionWorker
from knowledge_assistant.config import (
    AnswerPromptVersion,
    ConfigurationError,
    Settings,
    XArticleProviderName,
)
from knowledge_assistant.domain.chunks import MarkdownChunker
from knowledge_assistant.domain.documents import SourceProvider
from knowledge_assistant.domain.query import CitationValidator, ContextPolicy
from knowledge_assistant.domain.retrieval import (
    DiversityReranker,
    RetrievalStrategyName,
)
from knowledge_assistant.domain.sources import SourceClassifier, UnsupportedSourceError
from knowledge_assistant.infrastructure.extraction.article import (
    ArticleExtractor,
    MediumArticleExtractor,
    SubstackArticleExtractor,
    XArticleExtractor,
)
from knowledge_assistant.infrastructure.http.medium_feed_fallback import (
    MediumFeedFallbackFetcher,
)
from knowledge_assistant.infrastructure.http.provider_router import ProviderSourceFetcher
from knowledge_assistant.infrastructure.http.safe_fetcher import SafeHttpFetcher
from knowledge_assistant.infrastructure.http.safe_image_fetcher import SafeImageFetcher
from knowledge_assistant.infrastructure.http.tempo_xquik_article_provider import (
    TempoXquikArticleProvider,
)
from knowledge_assistant.infrastructure.http.x_article_fetcher import XArticleFetcher
from knowledge_assistant.infrastructure.http.xquik_article_provider import (
    XquikArticleProvider,
)
from knowledge_assistant.infrastructure.openai.answers import (
    OpenAIAnswerGenerator,
    OpenAIAnswerGeneratorV2,
)
from knowledge_assistant.infrastructure.openai.embeddings import OpenAIEmbeddingProvider
from knowledge_assistant.infrastructure.openai.evaluation import (
    OpenAIAnswerJudge,
    OpenAISyntheticQuestionGenerator,
    OpenAISyntheticQuestionGeneratorV2,
    OpenAISyntheticQuestionNaturalizer,
)
from knowledge_assistant.infrastructure.openai.planning import OpenAIQueryPlanner
from knowledge_assistant.infrastructure.postgres.evaluation_repository import (
    PostgresEvaluationRepository,
)
from knowledge_assistant.infrastructure.postgres.ingestion_repository import (
    PostgresIngestionRepository,
)
from knowledge_assistant.infrastructure.postgres.migrations import MigrationRunner
from knowledge_assistant.infrastructure.postgres.question_repository import (
    PostgresQuestionRepository,
)
from knowledge_assistant.infrastructure.telegram.client import TelegramClient
from knowledge_assistant.infrastructure.telemetry import (
    OpenTelemetryAdapter,
    current_trace_context,
)
from knowledge_assistant.infrastructure.vault.filesystem import FileSystemVaultRepository
from knowledge_assistant.ports.answers import AnswerGenerator
from knowledge_assistant.ports.evaluation import QuestionNaturalizer, SyntheticQuestionGenerator
from knowledge_assistant.ports.sources import XArticleProvider
from knowledge_assistant.ports.telemetry import NoOpTelemetry, Telemetry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledge-assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "check-config",
        help="validate runtime configuration and print a redacted summary",
    )
    subparsers.add_parser(
        "migrate",
        help="apply pending PostgreSQL schema migrations",
    )
    subparsers.add_parser("run-bot", help="run the Telegram long-polling service")
    subparsers.add_parser("run-worker", help="run the asynchronous ingestion worker")
    health = subparsers.add_parser("healthcheck", help="check a service heartbeat")
    health.add_argument("role", choices=("bot", "worker"))
    projection = subparsers.add_parser(
        "projection-rebuild",
        help="build a complete compatible retrieval projection",
    )
    projection.add_argument(
        "--activate",
        action="store_true",
        help="atomically activate after completeness validation",
    )
    projection_activate = subparsers.add_parser(
        "projection-activate",
        help="atomically activate an already validated retrieval projection",
    )
    projection_activate.add_argument("generation_id", type=UUID)
    generate_eval = subparsers.add_parser(
        "eval-generate",
        help="generate a private frozen JSONL dataset from stored chunks",
    )
    generate_eval.add_argument("--output", type=Path, required=True)
    generate_eval.add_argument("--count", type=int, default=20)
    generate_eval.add_argument("--seed", default="synthetic-chunks-v2")
    generate_eval.add_argument(
        "--version",
        choices=(
            SyntheticDatasetBuilder.DATASET_VERSION_V1,
            SyntheticDatasetBuilder.DATASET_VERSION_V2,
        ),
        default=SyntheticDatasetBuilder.DATASET_VERSION_V2,
        help="dataset schema version; v2 is the default for new datasets",
    )
    generate_eval.add_argument(
        "--no-naturalize",
        action="store_true",
        help="skip the source-blind naturalization pass for v2 datasets",
    )
    generate_eval.add_argument(
        "--style-weights",
        default=None,
        help=(
            "JSON object of question-style weights, e.g. "
            "'{\"fact\":0.4,\"explanation\":0.3,\"comparison\":0.2,\"exact_lookup\":0.1}'"
        ),
    )
    run_eval = subparsers.add_parser(
        "eval-run",
        help="compare retrieval strategies on a frozen JSONL dataset",
    )
    run_eval.add_argument("--dataset", type=Path, required=True)
    run_eval.add_argument("--output", type=Path, required=True)
    run_eval.add_argument(
        "--strategy",
        choices=("all", *(item.value for item in RetrievalStrategyName)),
        default="all",
    )
    run_eval.add_argument("--generation-id", type=UUID)
    answer_eval = subparsers.add_parser(
        "answer-eval-run",
        help="run end-to-end answer evaluation for grounded answer approaches",
    )
    answer_eval.add_argument("--dataset", type=Path, required=True)
    answer_eval.add_argument("--output", type=Path, required=True)
    answer_eval.add_argument("--output-markdown", type=Path, required=True)
    answer_eval.add_argument(
        "--strategy",
        choices=tuple(item.value for item in RetrievalStrategyName),
        default=RetrievalStrategyName.WEIGHTED_HYBRID.value,
    )
    answer_eval.add_argument(
        "--approaches",
        choices=("all", "grounded-answer-v1", "grounded-answer-v2"),
        default="all",
    )
    answer_eval.add_argument(
        "--judge-model",
        default=None,
        help="optional model for structured LLM judging of generated answers",
    )
    answer_eval.add_argument("--generation-id", type=UUID)
    calibrate = subparsers.add_parser(
        "answer-eval-calibrate",
        help="compare model-judge scores from answer-eval-run against reviewed human labels",
    )
    calibrate.add_argument("--results", type=Path, required=True)
    calibrate.add_argument("--human-labels", type=Path, required=True)
    calibrate.add_argument(
        "--output-markdown",
        type=Path,
        default=None,
        help="write a public-safe calibration markdown report",
    )
    sample_ingest = subparsers.add_parser(
        "sample-ingest",
        help="submit the public sample manifest sources to the ingestion queue",
    )
    sample_ingest.add_argument(
        "--manifest",
        type=Path,
        default=_default_manifest_path(),
    )
    sample_ingest.add_argument(
        "--recipient",
        type=int,
        default=None,
        help="numeric Telegram chat id for completion notifications "
        "(default: first allowlisted user id; omit for notification-free ingestion)",
    )
    sample_prepare = subparsers.add_parser(
        "sample-eval-prepare",
        help="build a document-level evaluation dataset from the sample manifest",
    )
    sample_prepare.add_argument(
        "--manifest",
        type=Path,
        default=_default_manifest_path(),
    )
    sample_prepare.add_argument("--output", type=Path, required=True)
    prefect_ingest = subparsers.add_parser(
        "prefect-ingest",
        help="run the optional Prefect flow to submit the sample manifest sources",
    )
    prefect_ingest.add_argument(
        "--manifest",
        type=Path,
        default=_default_manifest_path(),
    )
    prefect_ingest.add_argument(
        "--recipient",
        type=int,
        default=None,
        help="numeric Telegram chat id for completion notifications "
        "(default: first allowlisted user id; omit for notification-free ingestion)",
    )
    demo = subparsers.add_parser(
        "demo",
        help="non-Telegram reviewer demo over the real RAG path",
    )
    demo_subparsers = demo.add_subparsers(dest="demo_command", required=True)
    demo_ingest = demo_subparsers.add_parser(
        "ingest",
        help="submit the public sample manifest to the ingestion queue",
    )
    demo_ingest.add_argument(
        "--manifest",
        type=Path,
        default=_default_manifest_path(),
    )
    demo_ingest.add_argument(
        "--recipient",
        type=int,
        default=None,
        help="numeric Telegram chat id for completion notifications "
        "(default: first allowlisted user id; omit for notification-free ingestion)",
    )
    demo_ask = demo_subparsers.add_parser(
        "ask",
        help="ask one question through the real RAG path and print the grounded answer",
    )
    demo_ask.add_argument("--question", required=True)
    demo_ask.add_argument(
        "--strategy",
        choices=tuple(item.value for item in RetrievalStrategyName),
        default=RetrievalStrategyName.WEIGHTED_HYBRID.value,
    )
    return parser


def _default_manifest_path() -> Path:
    """Locate the committed sample manifest for both local and container runs.

    Local CLI runs use the repo-relative path; inside the Docker admin image
    (WORKDIR /app) the same relative path misses, so the compose-mounted
    ``/data/sample/manifest.json`` is used as a fallback.
    """

    relative = Path("data/sample/manifest.json")
    return relative if relative.exists() else Path("/data/sample/manifest.json")


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_environment(os.environ)
    except ConfigurationError as error:
        print(f"configuration error: {error}")
        return 2

    if args.command == "check-config":
        print(json.dumps(settings.redacted_summary(), indent=2, sort_keys=True))
        return 0
    if args.command == "migrate":
        applied = MigrationRunner(settings.database_url).apply()
        if applied:
            print(f"applied migrations: {', '.join(applied)}")
        else:
            print("database schema is up to date")
        return 0
    if args.command == "healthcheck":
        repository = PostgresIngestionRepository(settings.database_url)
        try:
            return 0 if repository.role_is_healthy(args.role) else 1
        finally:
            repository.close()
    if args.command == "projection-rebuild":
        try:
            return _run_projection_rebuild(settings, activate=bool(args.activate))
        except ConfigurationError as error:
            print(f"configuration error: {error}")
            return 2
    if args.command == "projection-activate":
        return _run_projection_activate(settings, generation_id=args.generation_id)
    if args.command == "eval-generate":
        try:
            return _run_eval_generate(
                settings,
                output=args.output,
                count=args.count,
                seed=args.seed,
                version=args.version,
                naturalize=not args.no_naturalize,
                style_weights=args.style_weights,
            )
        except ConfigurationError as error:
            print(f"configuration error: {error}")
            return 2
    if args.command == "eval-run":
        try:
            return _run_eval(
                settings,
                dataset=args.dataset,
                output=args.output,
                strategy=args.strategy,
                generation_id=args.generation_id,
            )
        except ConfigurationError as error:
            print(f"configuration error: {error}")
            return 2
    if args.command == "answer-eval-run":
        try:
            return _run_answer_eval(
                settings,
                dataset=args.dataset,
                output=args.output,
                output_markdown=args.output_markdown,
                strategy=args.strategy,
                approaches=args.approaches,
                judge_model=args.judge_model,
                generation_id=args.generation_id,
            )
        except ConfigurationError as error:
            print(f"configuration error: {error}")
            return 2
    if args.command == "answer-eval-calibrate":
        return _run_answer_calibrate(
            results=args.results,
            human_labels=args.human_labels,
            output_markdown=args.output_markdown,
        )
    if args.command == "sample-ingest":
        try:
            return _run_sample_ingest(
                settings,
                manifest=args.manifest,
                recipient=args.recipient,
            )
        except ConfigurationError as error:
            print(f"configuration error: {error}")
            return 2
    if args.command == "sample-eval-prepare":
        try:
            return _run_sample_prepare(settings, manifest=args.manifest, output=args.output)
        except ConfigurationError as error:
            print(f"configuration error: {error}")
            return 2
    if args.command == "prefect-ingest":
        try:
            return _run_prefect_ingest(
                settings,
                manifest=args.manifest,
                recipient=args.recipient,
            )
        except ConfigurationError as error:
            print(f"configuration error: {error}")
            return 2
    if args.command == "demo":
        if args.demo_command == "ingest":
            try:
                return _run_sample_ingest(
                    settings,
                    manifest=args.manifest,
                    recipient=args.recipient,
                )
            except ConfigurationError as error:
                print(f"configuration error: {error}")
                return 2
        if args.demo_command == "ask":
            try:
                return _run_demo_ask(
                    settings,
                    question=args.question,
                    strategy=args.strategy,
                )
            except ConfigurationError as error:
                print(f"configuration error: {error}")
                return 2
        return 2
    if args.command == "run-bot":
        try:
            return _run_bot(settings)
        except ConfigurationError as error:
            print(f"configuration error: {error}")
            return 2
    if args.command == "run-worker":
        try:
            return _run_worker(settings)
        except ConfigurationError as error:
            print(f"configuration error: {error}")
            return 2
    return 2


def _build_answer_generator(settings: Settings) -> AnswerGenerator:
    """Build the configured production answer generator.

    Single construction site for Telegram Question Mode and the CLI demo so
    both clients cannot silently diverge. `grounded-answer-v2` is the validated
    default; v1 is reachable only as an explicit config override.
    """

    assert settings.openai_api_key is not None
    assert settings.generation_model is not None
    if settings.answer_prompt_version is AnswerPromptVersion.GROUNDED_ANSWER_V1:
        return OpenAIAnswerGenerator(
            api_key=settings.openai_api_key,
            model=settings.generation_model,
        )
    if settings.answer_prompt_version is AnswerPromptVersion.GROUNDED_ANSWER_V2:
        return OpenAIAnswerGeneratorV2(
            api_key=settings.openai_api_key,
            model=settings.generation_model,
        )
    raise AssertionError(f"unhandled answer prompt version: {settings.answer_prompt_version}")


def _run_bot(settings: Settings) -> int:
    settings.require_bot()
    settings.require_question_service()
    assert settings.telegram_token is not None
    assert settings.openai_api_key is not None
    assert settings.embedding_model is not None
    assert settings.generation_model is not None
    _configure_logging()
    telemetry = _build_telemetry(settings, service_name="knowledge-assistant-bot")
    repository = PostgresIngestionRepository(settings.database_url)
    question_repository = PostgresQuestionRepository(settings.database_url)
    embeddings = OpenAIEmbeddingProvider(
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
    )
    planner = (
        OpenAIQueryPlanner(
            api_key=settings.openai_api_key,
            model=settings.generation_model,
        )
        if settings.retrieval_strategy.value == "agentic-decomposition-v1"
        else None
    )
    questions = QuestionService(
        repository=question_repository,
        retrieval=RetrievalOrchestrator(
            repository=question_repository,
            embeddings=embeddings,
            reranker=DiversityReranker(),
            planner=planner,
            telemetry=telemetry,
        ),
        generator=_build_answer_generator(settings),
        validator=CitationValidator(),
        session_ttl_seconds=settings.session_ttl_seconds,
        retrieval_strategy=settings.retrieval_strategy,
        telemetry=telemetry,
    )
    service = TelegramPollingService(
        telegram=TelegramClient(token=settings.telegram_token),
        repository=repository,
        classifier=SourceClassifier(),
        allowed_user_ids=settings.telegram_allowed_user_ids,
        poll_timeout_seconds=settings.telegram_poll_timeout_seconds,
        questions=questions,
        deletions=ArticleDeletionService(
            registry=repository,
            vault=FileSystemVaultRepository(settings.vault_path),
        ),
    )
    _install_signal_handlers(service.stop)
    try:
        service.run_forever()
    finally:
        repository.close()
        question_repository.close()
        telemetry.close()
    return 0


def _run_worker(settings: Settings) -> int:
    settings.require_worker()
    assert settings.openai_api_key is not None
    assert settings.embedding_model is not None
    _configure_logging()
    telemetry = _build_telemetry(settings, service_name="knowledge-assistant-worker")
    repository = PostgresIngestionRepository(settings.database_url)
    safe_fetcher = SafeHttpFetcher()
    article_provider: XArticleProvider
    if settings.x_article_provider is XArticleProviderName.XQUIK:
        assert settings.xquik_api_key is not None
        article_provider = XquikArticleProvider(api_key=settings.xquik_api_key)
    elif settings.x_article_provider is XArticleProviderName.XQUIK_MPP:
        article_provider = TempoXquikArticleProvider(
            max_spend_usdc=settings.xquik_mpp_max_spend_usdc,
        )
    service = IngestionWorker(
        repository=repository,
        classifier=SourceClassifier(),
        fetcher=ProviderSourceFetcher(
            {
                MediumArticleExtractor.PROVIDER: MediumFeedFallbackFetcher(safe_fetcher),
                SubstackArticleExtractor.PROVIDER: safe_fetcher,
                SourceProvider.WEB: safe_fetcher,
                XArticleExtractor.PROVIDER: XArticleFetcher(article_provider=article_provider),
            }
        ),
        extractors={
            MediumArticleExtractor.PROVIDER: MediumArticleExtractor(),
            SubstackArticleExtractor.PROVIDER: SubstackArticleExtractor(),
            SourceProvider.WEB: ArticleExtractor(),
            XArticleExtractor.PROVIDER: XArticleExtractor(),
        },
        asset_materializer=ArticleAssetMaterializer(SafeImageFetcher()),
        vault=FileSystemVaultRepository(settings.vault_path),
        chunker=MarkdownChunker(),
        embeddings=OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.embedding_model,
        ),
        telegram=(
            TelegramClient(token=settings.telegram_token)
            if settings.telegram_token is not None
            else None
        ),
        poll_seconds=settings.worker_poll_seconds,
        telemetry=telemetry,
    )
    _install_signal_handlers(service.stop)
    try:
        service.run_forever()
    finally:
        repository.close()
        telemetry.close()
    return 0


def _run_projection_rebuild(settings: Settings, *, activate: bool) -> int:
    settings.require_projection_service()
    assert settings.openai_api_key is not None
    assert settings.embedding_model is not None
    _configure_logging()
    telemetry = _build_telemetry(settings, service_name="knowledge-assistant-admin")
    repository = PostgresIngestionRepository(settings.database_url)
    try:
        generation_id = ProjectionRebuildService(
            repository=repository,
            vault=FileSystemVaultRepository(settings.vault_path),
            chunker=MarkdownChunker(),
            embeddings=OpenAIEmbeddingProvider(
                api_key=settings.openai_api_key,
                model=settings.embedding_model,
            ),
            telemetry=telemetry,
        ).rebuild(activate=activate)
    finally:
        repository.close()
        telemetry.close()
    print(
        json.dumps(
            {
                "generation_id": str(generation_id),
                "state": "active" if activate else "validated",
            },
            sort_keys=True,
        )
    )
    return 0


def _run_projection_activate(settings: Settings, *, generation_id: UUID) -> int:
    _configure_logging()
    repository = PostgresIngestionRepository(settings.database_url)
    try:
        repository.activate_projection_generation(generation_id)
    finally:
        repository.close()
    print(json.dumps({"generation_id": str(generation_id), "state": "active"}, sort_keys=True))
    return 0


def _run_eval_generate(
    settings: Settings,
    *,
    output: Path,
    count: int,
    seed: str,
    version: str,
    naturalize: bool,
    style_weights: str | None,
) -> int:
    settings.require_question_service()
    assert settings.openai_api_key is not None
    assert settings.generation_model is not None
    parsed_weights = _parse_style_weights(style_weights)
    telemetry = _build_telemetry(settings, service_name="knowledge-assistant-evaluation")
    corpus = PostgresEvaluationRepository(settings.database_url)
    try:
        generator: SyntheticQuestionGenerator
        naturalizer: QuestionNaturalizer | None
        if version == SyntheticDatasetBuilder.DATASET_VERSION_V1:
            generator = OpenAISyntheticQuestionGenerator(
                api_key=settings.openai_api_key,
                model=settings.generation_model,
            )
            naturalizer = None
        else:
            try:
                generator = OpenAISyntheticQuestionGeneratorV2(
                    api_key=settings.openai_api_key,
                    model=settings.generation_model,
                    style_weights=parsed_weights,
                )
            except ValueError as error:
                print(f"configuration error: {error}")
                return 2
            naturalizer = (
                OpenAISyntheticQuestionNaturalizer(
                    api_key=settings.openai_api_key,
                    model=settings.generation_model,
                )
                if naturalize
                else None
            )
        cases = SyntheticDatasetBuilder(
            corpus=corpus,
            generator=generator,
            naturalizer=naturalizer,
            version=version,
            telemetry=telemetry,
        ).build(count=count, seed=seed)
        write_jsonl(output, tuple(case.as_dict() for case in cases))
    finally:
        corpus.close()
        telemetry.close()
    print(json.dumps({"dataset": str(output), "cases": len(cases)}, sort_keys=True))
    return 0


def _parse_style_weights(raw: str | None) -> dict[str, float] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"--style-weights must be valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ConfigurationError("--style-weights must be a JSON object")
    weights: dict[str, float] = {}
    for key, item in parsed.items():
        try:
            value = float(item)
        except (TypeError, ValueError) as error:
            raise ConfigurationError(
                f"--style-weights values must be numbers: {key}={item!r}"
            ) from error
        weights[str(key)] = value
    return weights


def _run_eval(
    settings: Settings,
    *,
    dataset: Path,
    output: Path,
    strategy: str,
    generation_id: UUID | None,
) -> int:
    settings.require_question_service()
    assert settings.openai_api_key is not None
    assert settings.embedding_model is not None
    assert settings.generation_model is not None
    selected_strategies = (
        tuple(RetrievalStrategyName) if strategy == "all" else (RetrievalStrategyName(strategy),)
    )
    telemetry = _build_telemetry(settings, service_name="knowledge-assistant-evaluation")
    corpus = PostgresEvaluationRepository(settings.database_url)
    question_repository = PostgresQuestionRepository(settings.database_url)
    try:
        cases = load_dataset(dataset)
        runner = RetrievalEvaluationRunner(
            corpus=corpus,
            retrieval=RetrievalOrchestrator(
                repository=question_repository,
                embeddings=OpenAIEmbeddingProvider(
                    api_key=settings.openai_api_key,
                    model=settings.embedding_model,
                ),
                reranker=DiversityReranker(),
                planner=OpenAIQueryPlanner(
                    api_key=settings.openai_api_key,
                    model=settings.generation_model,
                ),
                telemetry=telemetry,
                candidate_limit=40,
                evidence_limit=20,
            ),
            telemetry=telemetry,
        )
        records: list[dict[str, object]] = []
        summaries: list[dict[str, object]] = []
        try:
            for selected_strategy in selected_strategies:
                results, summary = runner.run(
                    cases,
                    strategy=selected_strategy,
                    generation_id=generation_id,
                )
                records.extend(result.as_dict() for result in results)
                summaries.append(summary.as_dict())
        except RuntimeError as error:
            if "no active retrieval projection" in str(error):
                print(
                    json.dumps(
                        {"status": "not_run", "reason": str(error)},
                        sort_keys=True,
                    )
                )
                return 2
            raise
        except psycopg.OperationalError as error:
            print(
                json.dumps(
                    {"status": "not_run", "reason": f"database unavailable: {error}"},
                    sort_keys=True,
                )
            )
            return 2
        write_jsonl(output, tuple(records))
    finally:
        corpus.close()
        question_repository.close()
        telemetry.close()
    print(json.dumps({"results": str(output), "summaries": summaries}, sort_keys=True))
    return 0


def _run_answer_eval(
    settings: Settings,
    *,
    dataset: Path,
    output: Path,
    output_markdown: Path,
    strategy: str,
    approaches: str,
    judge_model: str | None,
    generation_id: UUID | None,
) -> int:
    settings.require_question_service()
    assert settings.openai_api_key is not None
    assert settings.embedding_model is not None
    assert settings.generation_model is not None
    selected_strategy = RetrievalStrategyName(strategy)
    selected_approaches = (
        ("grounded-answer-v1", "grounded-answer-v2")
        if approaches == "all"
        else (approaches,)
    )
    telemetry = _build_telemetry(settings, service_name="knowledge-assistant-evaluation")
    corpus = PostgresEvaluationRepository(settings.database_url)
    question_repository = PostgresQuestionRepository(settings.database_url)
    try:
        cases = load_dataset(dataset)
        generators: dict[str, AnswerGenerator] = {
            "grounded-answer-v1": OpenAIAnswerGenerator(
                api_key=settings.openai_api_key,
                model=settings.generation_model,
            ),
            "grounded-answer-v2": OpenAIAnswerGeneratorV2(
                api_key=settings.openai_api_key,
                model=settings.generation_model,
            ),
        }
        context_policies: dict[str, ContextPolicy] = {
            "grounded-answer-v1": ContextPolicy(total_limit=16_000, per_item_limit=2_400),
            "grounded-answer-v2": ContextPolicy(total_limit=12_000, per_item_limit=1_600),
        }
        judge = (
            OpenAIAnswerJudge(api_key=settings.openai_api_key, model=judge_model)
            if judge_model
            else None
        )
        runner = AnswerEvaluationRunner(
            corpus=corpus,
            retrieval=RetrievalOrchestrator(
                repository=question_repository,
                embeddings=OpenAIEmbeddingProvider(
                    api_key=settings.openai_api_key,
                    model=settings.embedding_model,
                ),
                reranker=DiversityReranker(),
                planner=OpenAIQueryPlanner(
                    api_key=settings.openai_api_key,
                    model=settings.generation_model,
                ),
                telemetry=telemetry,
                candidate_limit=40,
                evidence_limit=20,
            ),
            generators=generators,
            context_policies=context_policies,
            validator=CitationValidator(),
            judge=judge,
            telemetry=telemetry,
        )
        results, summaries = runner.run(
            cases,
            strategy=selected_strategy,
            generation_id=generation_id,
            approaches=selected_approaches,
        )
        write_jsonl(output, tuple(result.as_dict() for result in results))
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(
            render_answer_evaluation_markdown(summaries),
            encoding="utf-8",
        )
    finally:
        corpus.close()
        question_repository.close()
        telemetry.close()
    print(
        json.dumps(
            {
                "results": str(output),
                "markdown": str(output_markdown),
                "summaries": [summary.as_dict() for summary in summaries],
            },
            sort_keys=True,
        )
    )
    return 0


def _resolve_recipient(settings: Settings, recipient: int | None) -> int | None:
    """Resolve the completion-notification recipient for sample/prefect ingestion.

    Explicit ``--recipient`` wins; otherwise the first allowlisted user is used
    when Telegram is configured; otherwise ``None`` means notification-free
    ingestion (no fake recipient such as chat ID 0 is ever used).
    """

    if recipient is not None:
        return recipient
    allowed = sorted(settings.telegram_allowed_user_ids)
    return allowed[0] if allowed else None


def _run_answer_calibrate(
    *,
    results: Path,
    human_labels: Path,
    output_markdown: Path | None,
) -> int:
    """Compare real judge scores to reviewed human labels (calibration)."""

    judge_results: list[tuple[str, str, dict[str, object]]] = []
    for line in results.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        judge = row.get("judge")
        if not isinstance(judge, dict):
            continue
        scores = {
            dimension: float(judge[dimension])
            for dimension in _JUDGE_DIMENSIONS
            if isinstance(judge.get(dimension), (int, float))
        }
        if len(scores) == len(_JUDGE_DIMENSIONS):
            metadata = {
                key: judge[key]
                for key in ("model", "prompt_version", "rubric_version")
                if isinstance(judge.get(key), str)
            }
            judge_results.append(
                (
                    str(row["case_id"]),
                    str(row["approach"]),
                    {**scores, **metadata},
                )
            )
    try:
        labels = load_human_labels(human_labels)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "not_run", "reason": str(error)}, sort_keys=True))
        return 2
    report = calibrate_judge_scores(labels, judge_results)
    if report.human_label_count == 0:
        print(
            json.dumps(
                {
                    "status": "not_run",
                    "reason": "no reviewed human labels yet; judge scores remain uncalibrated",
                },
                sort_keys=True,
            )
        )
    else:
        print(json.dumps(report, default=asdict, sort_keys=True))
    if output_markdown is not None:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_calibration_markdown(report), encoding="utf-8")
    return 0


def _run_sample_ingest(settings: Settings, *, manifest: Path, recipient: int | None) -> int:
    try:
        sample = load_sample_manifest(manifest)
    except (ValueError, OSError) as error:
        print(f"sample manifest error: {error}")
        return 2
    resolved_recipient = _resolve_recipient(settings, recipient)
    classifier = SourceClassifier()
    repository = PostgresIngestionRepository(settings.database_url)
    submitted = 0
    already_pending = 0
    try:
        for source in sample.sources:
            try:
                classified = classifier.classify(source.url)
            except UnsupportedSourceError as error:
                print(
                    json.dumps(
                        {
                            "status": "not_run",
                            "reason": f"{source.source_id}: {error}",
                        },
                        sort_keys=True,
                    )
                )
                return 2
            submission = repository.submit(
                idempotency_key=f"sample:{source.source_id}",
                source=classified,
                recipient_key=(
                    str(resolved_recipient) if resolved_recipient is not None else None
                ),
                request_message_id=("0" if resolved_recipient is not None else None),
            )
            if submission.created:
                submitted += 1
            else:
                already_pending += 1
    except Exception as error:
        print(
            json.dumps(
                {"status": "not_run", "reason": f"database unavailable: {error}"},
                sort_keys=True,
            )
        )
        return 2
    finally:
        repository.close()
    print(
        json.dumps(
            {
                "status": "ok",
                "submitted": submitted,
                "already_pending": already_pending,
            },
            sort_keys=True,
        )
    )
    return 0


def _run_sample_prepare(settings: Settings, *, manifest: Path, output: Path) -> int:
    del settings
    try:
        sample = load_sample_manifest(manifest)
        cases = sample_cases_to_dataset(sample)
    except (ValueError, OSError) as error:
        print(f"sample manifest error: {error}")
        return 2
    write_jsonl(output, tuple(case.as_dict() for case in cases))
    print(
        json.dumps(
            {
                "dataset": str(output),
                "cases": len(cases),
                "dataset_version": sample.dataset_version,
            },
            sort_keys=True,
        )
    )
    return 0


def _run_prefect_ingest(
    settings: Settings,
    *,
    manifest: Path,
    recipient: int | None,
) -> int:
    try:
        from knowledge_assistant.infrastructure.orchestration.prefect_flow import (
            ingest_sample_corpus_flow,
        )
    except ImportError as error:
        print(
            "configuration error: prefect is not installed; run "
            f"'uv sync --extra orchestration': {error}"
        )
        return 2
    resolved_recipient = _resolve_recipient(settings, recipient)
    try:
        result = ingest_sample_corpus_flow(
            manifest_path=str(manifest),
            database_url=settings.database_url,
            recipient=resolved_recipient,
        )
    except Exception as error:
        print(
            json.dumps(
                {"status": "not_run", "reason": f"prefect flow failed: {error}"},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps({"status": "ok", **result}, sort_keys=True))
    return 0


def _run_demo_ask(
    settings: Settings,
    *,
    question: str,
    strategy: str,
) -> int:
    settings.require_question_service()
    assert settings.openai_api_key is not None
    assert settings.embedding_model is not None
    assert settings.generation_model is not None
    strategy_name = RetrievalStrategyName(strategy)
    telemetry = _build_telemetry(settings, service_name="knowledge-assistant-admin")
    repository = PostgresQuestionRepository(settings.database_url)
    try:
        questions = QuestionService(
            repository=repository,
            retrieval=RetrievalOrchestrator(
                repository=repository,
                embeddings=OpenAIEmbeddingProvider(
                    api_key=settings.openai_api_key,
                    model=settings.embedding_model,
                ),
                reranker=DiversityReranker(),
                planner=(
                    OpenAIQueryPlanner(
                        api_key=settings.openai_api_key,
                        model=settings.generation_model,
                    )
                    if strategy_name.value == "agentic-decomposition-v1"
                    else None
                ),
                telemetry=telemetry,
            ),
            generator=_build_answer_generator(settings),
            validator=CitationValidator(),
            session_ttl_seconds=settings.session_ttl_seconds,
            retrieval_strategy=strategy_name,
            telemetry=telemetry,
        )
        result = questions.ask_once(question=question)
    finally:
        repository.close()
        telemetry.close()
    print(result.rendered_text)
    return 0


def _install_signal_handlers(stop: object) -> None:
    callback = stop
    if not callable(callback):
        raise TypeError("stop must be callable")

    def handle_signal(_signum: int, _frame: object) -> None:
        callback()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Telegram includes the bot token in its Bot API URL path. Transport-level
    # request logs must therefore never be emitted, even at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _build_telemetry(settings: Settings, *, service_name: str) -> Telemetry:
    endpoint = settings.otel_exporter_otlp_endpoint
    if endpoint is None:
        return NoOpTelemetry()
    return OpenTelemetryAdapter(service_name=service_name, endpoint=endpoint)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        trace_id, span_id = current_trace_context()
        if trace_id is not None:
            payload["trace_id"] = trace_id
        if span_id is not None:
            payload["span_id"] = span_id
        return json.dumps(payload, ensure_ascii=True)
