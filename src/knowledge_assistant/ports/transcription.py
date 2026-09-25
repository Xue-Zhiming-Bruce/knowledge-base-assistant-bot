"""Speech-to-text contracts owned by the application boundary."""

from __future__ import annotations

from typing import Protocol

from knowledge_assistant.domain.podcasts import Transcript


class TranscriptionProvider(Protocol):
    """Transcribe one complete audio payload into text."""

    def transcribe(
        self, audio: bytes, mime: str, prompt: str | None = None
    ) -> Transcript:
        """Transcribe one audio payload or raise a typed transcription error.

        ``prompt`` is unstructured context (episode title, vocabulary) that helps
        the model spell proper nouns correctly. The returned ``Transcript``
        carries the language the model detected, when it reports one.
        """
