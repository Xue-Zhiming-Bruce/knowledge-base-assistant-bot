"""Embedding provider contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...]
    model: str
    dimensions: int
    input_tokens: int | None


class EmbeddingProvider(Protocol):
    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        """Embed non-empty text inputs in their original order."""
