"""Podcast episode contracts shared by resolver, transcriber, and worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PodcastEpisode:
    """One resolvable podcast episode with a publicly accessible audio URL."""

    title: str
    audio_url: str
    show: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    shownotes_markdown: str | None = None


@dataclass(frozen=True, slots=True)
class Transcript:
    """Transcript text plus the language the model reported, when it reports one.

    ``language`` is None for models that return no language field; callers must
    decide their own fallback rather than assume the audio is English.
    """

    text: str
    language: str | None = None
