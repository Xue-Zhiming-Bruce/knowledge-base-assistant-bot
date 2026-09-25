"""Podcast ingestion: resolve audio, transcribe, produce an ExtractedArticle."""

from __future__ import annotations

from typing import Any, cast

from knowledge_assistant.domain.podcasts import PodcastEpisode
from knowledge_assistant.domain.sources import (
    ClassifiedSource,
    ExtractedArticle,
    SourceFetchError,
)
from knowledge_assistant.infrastructure.http.podcast_resolver import (
    PodcastAudioDownloader,
    PodcastEpisodeResolver,
)
from knowledge_assistant.ports.transcription import TranscriptionProvider

# Episode titles carry the proper nouns ASR models otherwise mangle in Chinese
# audio (BitMEX/Bybit/永续合约). Titles come from remote pages, so the prompt is
# whitespace-collapsed and bounded before it is sent to the API.
_MAX_PROMPT_CHARS = 400


def _format_duration(seconds: int | None) -> str | None:
    if seconds is None or seconds <= 0:
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class PodcastService:
    """Turn a podcast source into a transcript article for the normal pipeline."""

    def __init__(
        self,
        *,
        transcriber: TranscriptionProvider,
        transcriber_model: str,
        resolver: PodcastEpisodeResolver | None = None,
        downloader: Any = None,
    ) -> None:
        self._resolver = resolver or PodcastEpisodeResolver()
        self._downloader = downloader or PodcastAudioDownloader()
        self._downloader = cast("PodcastAudioDownloader", self._downloader)
        self._transcriber = transcriber
        self.transcriber_model = transcriber_model

    @property
    def extractor_label(self) -> str:
        return f"podcast-transcript-{self.transcriber_model}"

    @staticmethod
    def _prompt(episode: PodcastEpisode) -> str | None:
        text = " ".join(part for part in (episode.title, episode.show) if part)
        collapsed = " ".join(text.split())[:_MAX_PROMPT_CHARS]
        return collapsed or None

    def probe_substack(self, source: ClassifiedSource) -> PodcastEpisode | None:
        return self._resolver.probe_substack(source)

    def resolve(self, source: ClassifiedSource) -> PodcastEpisode:
        return self._resolver.resolve(source)

    def transcribe(self, episode: PodcastEpisode, canonical_url: str) -> ExtractedArticle:
        audio, mime = self._downloader.download(episode.audio_url)
        transcript = self._transcriber.transcribe(audio, mime, self._prompt(episode))

        header_lines = [f"- Podcast: {episode.show}" if episode.show else None]
        if episode.published_at is not None:
            header_lines.append(
                f"- Published: {episode.published_at.date().isoformat()}"
            )
        duration = _format_duration(episode.duration_seconds)
        if duration is not None:
            header_lines.append(f"- Duration: {duration}")
        header_lines.append(f"- Source: [{canonical_url}]({canonical_url})")
        sections = ["\n".join(line for line in header_lines if line)]
        if episode.shownotes_markdown:
            sections.append("## Show notes\n\n" + episode.shownotes_markdown)
        sections.append("## Transcript\n\n" + transcript.text.strip())
        return ExtractedArticle(
            title=episode.title,
            markdown="\n\n".join(sections),
            authors=(episode.show,) if episode.show else (),
            published_at=episode.published_at,
            canonical_url=canonical_url,
            language=transcript.language,
        )


__all__ = ["PodcastService", "SourceFetchError"]
