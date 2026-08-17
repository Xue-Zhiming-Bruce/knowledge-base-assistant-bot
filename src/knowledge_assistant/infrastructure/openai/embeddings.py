"""OpenAI embeddings adapter."""

from __future__ import annotations

from openai import OpenAI

from knowledge_assistant.ports.embeddings import EmbeddingBatch


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: OpenAI | None = None,
    ) -> None:
        self._model = model
        self._client = client or OpenAI(api_key=api_key, max_retries=3, timeout=30)

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("embedding inputs must be non-empty")
        response = self._client.embeddings.create(
            model=self._model,
            input=list(texts),
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = tuple(tuple(float(value) for value in item.embedding) for item in ordered)
        if len(vectors) != len(texts):
            raise RuntimeError("embedding provider returned an unexpected number of vectors")
        dimensions = len(vectors[0])
        if any(len(vector) != dimensions for vector in vectors):
            raise RuntimeError("embedding provider returned inconsistent dimensions")
        return EmbeddingBatch(
            vectors=vectors,
            model=response.model,
            dimensions=dimensions,
            input_tokens=response.usage.prompt_tokens if response.usage is not None else None,
        )
