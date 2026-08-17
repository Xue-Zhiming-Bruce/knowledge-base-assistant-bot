from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Any, cast

import pytest

from knowledge_assistant.application.projections import ProjectionRebuildService
from knowledge_assistant.domain.chunks import MarkdownChunker
from knowledge_assistant.domain.documents import RevisionId
from knowledge_assistant.infrastructure.postgres.ingestion_repository import ProjectionDocument
from knowledge_assistant.infrastructure.telemetry import (
    OpenTelemetryAdapter,
    current_trace_context,
)
from knowledge_assistant.ports.embeddings import EmbeddingBatch
from knowledge_assistant.ports.vault import StoredKnowledgeDocument
from tests.factories import knowledge_document


class ProjectionRepository:
    def __init__(self) -> None:
        document = knowledge_document()
        self.reference = ProjectionDocument(
            document_id=document.revision.document_id.value,
            revision_id=document.revision.revision_id.value,
            vault_path="Articles/medium/document.md",
        )
        self.generation_id = uuid.uuid4()
        self.stored = 0
        self.build_kwargs: dict[str, object] = {}
        self.store_kwargs: dict[str, object] = {}
        self.validated = False
        self.activated = False
        self.failed = False

    def projection_documents(self) -> tuple[ProjectionDocument, ...]:
        return (self.reference,)

    def building_projection_generation(self, **kwargs: object) -> uuid.UUID:
        self.build_kwargs = kwargs
        return self.generation_id

    def store_chunks(self, **kwargs: object) -> None:
        self.store_kwargs = kwargs
        self.stored += 1

    def validate_projection_generation(self, generation_id: uuid.UUID) -> None:
        assert generation_id == self.generation_id
        self.validated = True

    def activate_projection_generation(self, generation_id: uuid.UUID) -> None:
        assert generation_id == self.generation_id
        self.activated = True

    def fail_projection_generation(self, generation_id: uuid.UUID) -> None:
        assert generation_id == self.generation_id
        self.failed = True


class ProjectionVault:
    def __init__(self, *, mismatched: bool = False) -> None:
        document = knowledge_document()
        if mismatched:
            object.__setattr__(
                document.revision,
                "revision_id",
                RevisionId("rev_" + "f" * 32),
            )
        self.stored = StoredKnowledgeDocument(
            document=document,
            vault_path=PurePosixPath("Articles/medium/document.md"),
            file_fingerprint="sha256:" + "a" * 64,
        )

    def read(self, _path: PurePosixPath) -> StoredKnowledgeDocument:
        return self.stored


class ProjectionEmbeddings:
    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=tuple((0.1, 0.2) for _text in texts),
            model="embedding-test",
            dimensions=2,
            input_tokens=len(texts),
        )


def test_projection_rebuild_validates_before_optional_activation() -> None:
    repository = ProjectionRepository()
    result = ProjectionRebuildService(
        repository=cast(Any, repository),
        vault=cast(Any, ProjectionVault()),
        chunker=MarkdownChunker(),
        embeddings=ProjectionEmbeddings(),
    ).rebuild(activate=True)

    assert result == repository.generation_id
    assert repository.stored == 1
    assert repository.validated
    assert repository.activated


def test_projection_rebuild_rejects_empty_or_changed_corpus() -> None:
    empty = ProjectionRepository()
    empty.projection_documents = lambda: ()  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="without documents"):
        ProjectionRebuildService(
            repository=cast(Any, empty),
            vault=cast(Any, ProjectionVault()),
            chunker=MarkdownChunker(),
            embeddings=ProjectionEmbeddings(),
        ).rebuild()

    changed = ProjectionRepository()
    with pytest.raises(RuntimeError, match="changed"):
        ProjectionRebuildService(
            repository=cast(Any, changed),
            vault=cast(Any, ProjectionVault(mismatched=True)),
            chunker=MarkdownChunker(),
            embeddings=ProjectionEmbeddings(),
        ).rebuild()


def test_projection_rebuild_marks_started_candidate_failed() -> None:
    class FailingProjectionRepository(ProjectionRepository):
        def store_chunks(self, **_kwargs: object) -> None:
            raise RuntimeError("index write failed")

    repository = FailingProjectionRepository()
    with pytest.raises(RuntimeError, match="index write failed"):
        ProjectionRebuildService(
            repository=cast(Any, repository),
            vault=cast(Any, ProjectionVault()),
            chunker=MarkdownChunker(),
            embeddings=ProjectionEmbeddings(),
        ).rebuild()

    assert repository.failed


class FakeCounter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, object]]] = []

    def add(self, value: int, attributes: dict[str, object]) -> None:
        self.calls.append((value, attributes))


class FakeHistogram:
    def __init__(self) -> None:
        self.calls: list[tuple[float, dict[str, object]]] = []

    def record(self, value: float, attributes: dict[str, object]) -> None:
        self.calls.append((value, attributes))


class FakeMeter:
    def __init__(self) -> None:
        self.counters: dict[str, FakeCounter] = {}
        self.histograms: dict[str, FakeHistogram] = {}

    def create_counter(self, name: str) -> FakeCounter:
        return self.counters.setdefault(name, FakeCounter())

    def create_histogram(self, name: str) -> FakeHistogram:
        return self.histograms.setdefault(name, FakeHistogram())


class FakeTracer:
    @contextmanager
    def start_as_current_span(self, name: str, **_kwargs: object) -> Any:
        yield name


class FakeTraceProvider:
    def __init__(self, **_kwargs: object) -> None:
        self.tracer = FakeTracer()

    def add_span_processor(self, _processor: object) -> None:
        return None

    def get_tracer(self, _name: str) -> FakeTracer:
        return self.tracer

    def shutdown(self) -> None:
        return None


class FakeMeterProvider:
    def __init__(self, **_kwargs: object) -> None:
        self.meter = FakeMeter()

    def get_meter(self, _name: str) -> FakeMeter:
        return self.meter

    def shutdown(self) -> None:
        return None


def test_opentelemetry_adapter_caches_instruments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.telemetry.Resource.create",
        lambda _attributes: object(),
    )
    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.telemetry.TracerProvider",
        FakeTraceProvider,
    )
    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.telemetry.MeterProvider",
        FakeMeterProvider,
    )
    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.telemetry.OTLPSpanExporter",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.telemetry.OTLPMetricExporter",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.telemetry.BatchSpanProcessor",
        lambda _exporter: object(),
    )
    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.telemetry.PeriodicExportingMetricReader",
        lambda _exporter: object(),
    )

    telemetry = OpenTelemetryAdapter(
        service_name="test-service",
        endpoint="http://collector:4318/",
    )
    with telemetry.span("operation", {"outcome": "ok"}) as span:
        assert span == "operation"
    telemetry.count("requests", attributes={"outcome": "ok"})
    telemetry.count("requests", value=2)
    telemetry.observe("latency", 0.2, {"stage": "test"})
    telemetry.observe("latency", 0.3)

    meter = cast(FakeMeter, telemetry._meter)
    assert len(meter.counters) == 1
    assert meter.counters["requests"].calls[1][0] == 2
    assert len(meter.histograms["latency"].calls) == 2
    assert current_trace_context() == (None, None)
    telemetry.close()
