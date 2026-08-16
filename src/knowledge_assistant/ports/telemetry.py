"""Engine-owned observability contract."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

type AttributeValue = str | bool | int | float
type Attributes = Mapping[str, AttributeValue]


class Telemetry(Protocol):
    """Record bounded traces and metrics without affecting domain outcomes."""

    def span(
        self,
        name: str,
        attributes: Attributes | None = None,
    ) -> AbstractContextManager[object]:
        """Create a span that never contains prompts, content, or credentials."""

    def count(
        self,
        name: str,
        value: int = 1,
        attributes: Attributes | None = None,
    ) -> None:
        """Increment a counter."""

    def observe(
        self,
        name: str,
        value: float,
        attributes: Attributes | None = None,
    ) -> None:
        """Record a histogram observation."""

    def close(self) -> None:
        """Flush and release exporters; implementations must be idempotent."""


class NoOpTelemetry:
    """Deterministic adapter for tests and disabled telemetry."""

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Attributes | None = None,
    ) -> Iterator[object]:
        del name, attributes
        yield self

    def count(
        self,
        name: str,
        value: int = 1,
        attributes: Attributes | None = None,
    ) -> None:
        del name, value, attributes
        return None

    def observe(
        self,
        name: str,
        value: float,
        attributes: Attributes | None = None,
    ) -> None:
        del name, value, attributes
        return None

    def close(self) -> None:
        return None
