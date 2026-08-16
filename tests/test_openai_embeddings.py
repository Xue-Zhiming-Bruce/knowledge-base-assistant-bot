from types import SimpleNamespace
from typing import Any, cast

import pytest

from knowledge_assistant.infrastructure.openai.embeddings import OpenAIEmbeddingProvider


class FakeEmbeddingsResource:
    def create(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=[0.3, 0.4]),
                SimpleNamespace(index=0, embedding=[0.1, 0.2]),
            ],
            model="text-embedding-test",
            usage=SimpleNamespace(prompt_tokens=8),
        )


def test_openai_embedding_adapter_preserves_input_order() -> None:
    client = SimpleNamespace(embeddings=FakeEmbeddingsResource())
    provider = OpenAIEmbeddingProvider(
        api_key="unused",
        model="text-embedding-test",
        client=cast(Any, client),
    )

    batch = provider.embed(("first", "second"))

    assert batch.vectors == ((0.1, 0.2), (0.3, 0.4))
    assert batch.input_tokens == 8


def test_openai_embedding_adapter_rejects_empty_inputs() -> None:
    provider = OpenAIEmbeddingProvider(
        api_key="unused",
        model="test",
        client=cast(Any, SimpleNamespace()),
    )
    with pytest.raises(ValueError, match="non-empty"):
        provider.embed(())
