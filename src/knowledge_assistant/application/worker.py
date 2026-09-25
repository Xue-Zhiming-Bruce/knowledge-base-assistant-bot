"""Asynchronous ingestion worker."""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from collections.abc import Mapping
from importlib.metadata import version
from pathlib import PurePosixPath
from time import perf_counter
from uuid import uuid4

import httpx
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from knowledge_assistant.application.assets import ArticleAssetMaterializer
from knowledge_assistant.application.podcasts import PodcastService
from knowledge_assistant.domain.chunks import MarkdownChunker
from knowledge_assistant.domain.documents import (
    DEFAULT_LANGUAGE,
    DocumentId,
    IngestionProvenance,
    KnowledgeDocument,
    SourceProvider,
    SourceReference,
)
from knowledge_assistant.domain.errors import DocumentConflictError
from knowledge_assistant.domain.ingestion import IngestionState
from knowledge_assistant.domain.podcasts import PodcastEpisode
from knowledge_assistant.domain.sources import (
    ClassifiedSource,
    ExtractionError,
    SourceClassifier,
    SourceFetchError,
)
from knowledge_assistant.infrastructure.extraction.article import ArticleExtractor
from knowledge_assistant.infrastructure.openai.topic_classifier import OpenAITopicClassifier
from knowledge_assistant.infrastructure.postgres.ingestion_repository import (
    ClaimedJob,
    PostgresIngestionRepository,
)
from knowledge_assistant.infrastructure.telegram.client import TelegramApiError, TelegramClient
from knowledge_assistant.ports.embeddings import EmbeddingProvider
from knowledge_assistant.ports.sources import SourceFetcher
from knowledge_assistant.ports.telemetry import NoOpTelemetry, Telemetry
from knowledge_assistant.ports.vault import VaultRepository


class IngestionWorker:
    """Run the full source-to-canonical-document projection pipeline."""

    def __init__(
        self,
        *,
        repository: PostgresIngestionRepository,
        classifier: SourceClassifier,
        fetcher: SourceFetcher,
        extractors: Mapping[SourceProvider, ArticleExtractor],
        asset_materializer: ArticleAssetMaterializer,
        vault: VaultRepository,
        chunker: MarkdownChunker,
        embeddings: EmbeddingProvider,
        topic_classifier: OpenAITopicClassifier | None = None,
        telegram: TelegramClient | None = None,
        podcast: PodcastService | None = None,
        poll_seconds: float,
        instance_id: str | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self._repository = repository
        self._classifier = classifier
        self._fetcher = fetcher
        self._extractors = dict(extractors)
        self._asset_materializer = asset_materializer
        self._vault = vault
        self._chunker = chunker
        self._embeddings = embeddings
        self._topic_classifier = topic_classifier
        self._telegram = telegram
        self._podcast = podcast
        self._poll_seconds = poll_seconds
        self._instance_id = instance_id or f"worker-{uuid4()}"
        self._telemetry = telemetry or NoOpTelemetry()
        self._stop = threading.Event()
        self._logger = logging.getLogger(__name__)

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self._repository.heartbeat(role="worker", instance_id=self._instance_id)
            self.dispatch_notifications()
            job = self._repository.claim(worker_id=self._instance_id)
            if job is None:
                self._stop.wait(self._poll_seconds)
                continue
            self.process_job(job)

    def process_job(self, job: ClaimedJob) -> None:
        started = perf_counter()
        with self._telemetry.span(
            "ingestion.attempt",
            {
                "source.provider": job.source_provider,
                "ingestion.attempt": job.attempt_count,
            },
        ):
            self._process_job(job)
        self._telemetry.observe(
            "ingestion_stage_duration_seconds",
            perf_counter() - started,
            {
                "stage": "total",
                "source_provider": job.source_provider,
                "outcome": "complete",
            },
        )

    def _process_job(self, job: ClaimedJob) -> None:
        try:
            source = self._classifier.classify(job.source_url)
            episode = self._select_podcast_episode(job, source)
            if episode is not None:
                assert self._podcast is not None
                self._repository.transition(
                    job.job_id,
                    expected=IngestionState.FETCHING,
                    target=IngestionState.EXTRACTING,
                )
                article = self._podcast.transcribe(episode, source.canonical_url)
                extractor_name = self._podcast.extractor_label
                extractor_version = f"transcript-{self._podcast.transcriber_model}"
            else:
                fetched = self._fetcher.fetch(source)
                self._repository.transition(
                    job.job_id,
                    expected=IngestionState.FETCHING,
                    target=IngestionState.EXTRACTING,
                )
                article = self._extractors[source.provider].extract(fetched)
                extractor_name = None
                extractor_version = (
                    f"xquik-article+markdownify-{version('markdownify')}"
                    if source.provider is SourceProvider.X
                    else version("markdownify")
                )
            self._repository.transition(
                job.job_id,
                expected=IngestionState.EXTRACTING,
                target=IngestionState.NORMALIZING,
            )
            document_id = self._repository.find_document_id(
                source.normalized_source_key
            ) or DocumentId.derive_from_source(source.normalized_source_key)
            existing = self._vault.find_by_document_id(document_id)
            if existing is not None:
                vault_path = existing.vault_path
                topic: str | None = None
            else:
                topic = self._choose_topic(source.provider, article.title, article.markdown)
                vault_path = self._vault_path(
                    self._vault_folder(source, podcast=episode is not None),
                    article.title,
                    document_id,
                    topic=topic,
                    root="Podcasts" if episode is not None else "Articles",
                )
            materialized = self._asset_materializer.materialize(
                article,
                document_id=document_id,
            )
            document = KnowledgeDocument.create(
                document_id=document_id,
                title=article.title,
                markdown_body=materialized.markdown,
                source=SourceReference(
                    url=source.canonical_url,
                    source_type=source.source_type,
                    provider=source.provider,
                ),
                authors=article.authors,
                published_at=article.published_at,
                ingestion=IngestionProvenance(
                    extractor=extractor_name or f"{source.provider.value}-markdownify",
                    extractor_version=extractor_version,
                    normalizer_version="markdown-assets-v6",
                ),
                assets=materialized.metadata,
                language=article.language or DEFAULT_LANGUAGE,
            )
            self._repository.transition(
                job.job_id,
                expected=IngestionState.NORMALIZING,
                target=IngestionState.VALIDATING,
            )
            self._repository.transition(
                job.job_id,
                expected=IngestionState.VALIDATING,
                target=IngestionState.COMMITTING,
            )
            stored = self._vault.commit_bundle(
                document,
                vault_path,
                materialized.vault_assets,
                expected_file_fingerprint=(
                    existing.file_fingerprint if existing is not None else None
                ),
            )
            self._repository.register_document(source=source, stored=stored)
            if topic is None and existing is None:
                self._repository.record_pending_filing(
                    job_id=job.job_id,
                    document_id=document_id,
                    title=article.title,
                    provider=source.provider.value,
                    pending_vault_path=vault_path.as_posix(),
                )
            self._repository.transition(
                job.job_id,
                expected=IngestionState.COMMITTING,
                target=IngestionState.INDEXING,
            )
            chunks = self._chunker.chunk(document)
            texts = tuple(chunk.content for chunk in chunks)
            batch = self._embeddings.embed(texts)
            generation_id = self._repository.ensure_projection_generation(
                embedding_model=batch.model,
                dimensions=batch.dimensions,
                chunker_version=self._chunker.VERSION,
            )
            self._repository.store_chunks(
                generation_id=generation_id,
                document=document,
                chunks=chunks,
                vectors=batch.vectors,
            )
            self._repository.mark_ready(job.job_id, document=document)
            self._telemetry.count(
                "ingestion_jobs_total",
                attributes={
                    "outcome": "ready",
                    "source_provider": job.source_provider,
                },
            )
            self._logger.info(
                "ingestion_ready job_id=%s document_id=%s chunks=%s "
                "assets=%s omitted_images=%s embedding_tokens=%s",
                job.job_id,
                document.revision.document_id,
                len(chunks),
                len(materialized.metadata),
                materialized.omitted_images,
                batch.input_tokens,
            )
            self.dispatch_notifications()
        except Exception as error:
            retryable = self._is_retryable(error)
            terminal = self._repository.fail_or_retry(
                job,
                error=error,
                retryable=retryable,
            )
            self._telemetry.count(
                "ingestion_jobs_total",
                attributes={
                    "outcome": terminal.value,
                    "source_provider": job.source_provider,
                },
            )
            self._logger.exception(
                "ingestion_failed job_id=%s retryable=%s state=%s",
                job.job_id,
                retryable,
                terminal.value,
            )
            if terminal is IngestionState.FAILED:
                self._notify_failure(job, error)

    def dispatch_notifications(self) -> None:
        if self._telegram is None:
            # No Telegram client configured: notification-free ingestion is an
            # explicit no-op (no fake recipient is ever fabricated).
            return
        for notification in self._repository.claim_notifications(worker_id=self._instance_id):
            try:
                self._telegram.send_message(
                    chat_id=int(notification.recipient_key),
                    text=notification.message,
                    reply_to_message_id=int(notification.request_message_id),
                )
            except (TelegramApiError, ValueError):
                self._repository.defer_notification(notification.notification_id)
                self._logger.exception(
                    "notification_deferred notification_id=%s",
                    notification.notification_id,
                )
            else:
                self._repository.mark_notification_delivered(notification.notification_id)

    def _notify_failure(self, job: ClaimedJob, error: Exception) -> None:
        if self._telegram is None:
            return
        for subscriber in self._repository.subscribers(job.job_id):
            try:
                self._telegram.send_message(
                    chat_id=int(subscriber.recipient_key),
                    text=f"I couldn't save that article: {str(error)[:300]}",
                    reply_to_message_id=int(subscriber.request_message_id),
                )
            except (TelegramApiError, ValueError):
                self._logger.exception(
                    "failure_notification_failed job_id=%s recipient=%s",
                    job.job_id,
                    subscriber.recipient_key,
                )

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        if isinstance(error, SourceFetchError):
            return error.retryable
        return isinstance(
            error,
            (
                APIConnectionError,
                APITimeoutError,
                InternalServerError,
                RateLimitError,
                httpx.NetworkError,
                httpx.TimeoutException,
            ),
        ) and not isinstance(error, (ExtractionError, DocumentConflictError))

    def _select_podcast_episode(
        self, job: ClaimedJob, source: ClassifiedSource
    ) -> PodcastEpisode | None:
        """Return a PodcastEpisode when this job should take the podcast path."""

        if self._podcast is None:
            if source.provider is SourceProvider.PODCAST:
                raise SourceFetchError(
                    "Podcast ingestion is not configured on this worker.",
                    retryable=False,
                )
            return None
        if source.provider is SourceProvider.PODCAST:
            return self._podcast.resolve(source)
        if source.provider is SourceProvider.SUBSTACK:
            episode = self._podcast.probe_substack(source)
            if episode is not None:
                # Podcast transcription outlives the normal article lease.
                self._repository.mark_podcast_job(job.job_id)
            return episode
        return None

    @staticmethod
    def _vault_folder(source: ClassifiedSource, *, podcast: bool) -> str:
        """Provider segment of the vault path: platform name for podcasts."""

        if not podcast:
            return source.provider.value
        key = source.normalized_source_key
        if key.startswith("podcast:xiaoyuzhou:"):
            return "xiaoyuzhou"
        if key.startswith("podcast:apple:"):
            return "apple-podcasts"
        return source.provider.value

    @staticmethod
    def _vault_path(
        provider: str,
        title: str,
        document_id: DocumentId,
        topic: str | None = None,
        *,
        root: str = "Articles",
    ) -> PurePosixPath:
        ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:80]
        slug = slug or "untitled"
        filename = f"{slug}-{document_id.value[-8:]}.md"
        if topic is None:
            return PurePosixPath(root, provider, "_Pending", filename)
        return PurePosixPath(root, provider, topic, filename)

    def _choose_topic(
        self,
        provider: SourceProvider,
        article_title: str,
        markdown: str,
    ) -> str | None:
        """Pick an existing vault topic, or None to file as pending.

        Classification failure degrades to a pending filing rather than
        failing ingestion: the article is still saved and can be filed later.
        """

        if self._topic_classifier is None:
            return None
        try:
            return self._topic_classifier.classify(
                title=article_title,
                content_excerpt=markdown[:4000],
                topics=self._vault.topic_names(),
            )
        except Exception:
            self._logger.exception("topic_classification_failed provider=%s", provider.value)
            return None
