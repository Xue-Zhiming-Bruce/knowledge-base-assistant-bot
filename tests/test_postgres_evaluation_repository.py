from __future__ import annotations

import uuid
from types import TracebackType
from typing import Any, cast

from knowledge_assistant.infrastructure.postgres.evaluation_repository import (
    PostgresEvaluationRepository,
)


class FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class FakeConnection:
    def __init__(self, generation_id: uuid.UUID) -> None:
        self.generation_id = generation_id
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

    def execute(self, query: Any, _params: object = None) -> FakeResult:
        statement = str(query)
        self.statements.append(statement)
        if "FROM projection_generations" in statement:
            return FakeResult([{"generation_id": self.generation_id}])
        if "WITH eligible" in statement:
            return FakeResult(
                [
                    {
                        "chunk_id": "chunk-a",
                        "document_id": "doc-a",
                        "revision_id": "rev-a",
                        "content": "A useful chunk.",
                        "content_fingerprint": "sha256:" + "a" * 64,
                        "token_count": 100,
                        "source_provider": "substack",
                    }
                ]
            )
        return FakeResult([{"exists": 1}])


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.closed = False

    def connection(self) -> FakeConnection:
        return self._connection

    def close(self) -> None:
        self.closed = True


def test_postgres_evaluation_repository_samples_and_validates_chunks() -> None:
    generation_id = uuid.uuid4()
    connection = FakeConnection(generation_id)
    pool = FakePool(connection)
    repository = PostgresEvaluationRepository(
        "postgresql://unused",
        pool=cast(Any, pool),
    )

    assert repository.active_generation_id() == generation_id
    sampled = repository.sample_chunks(
        generation_id=generation_id,
        count=1,
        seed="seed",
    )
    assert sampled[0].chunk_id == "chunk-a"
    assert repository.validate_chunk(
        generation_id=generation_id,
        chunk_id="chunk-a",
        content_fingerprint="sha256:" + "a" * 64,
    )
    assert any("md5" in statement for statement in connection.statements)
    repository.close()
    assert pool.closed
