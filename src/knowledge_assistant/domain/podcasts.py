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
