"""Speech-to-text contracts owned by the application boundary."""

from __future__ import annotations

from typing import Protocol


class TranscriptionProvider(Protocol):
    """Transcribe one complete audio payload into plain text."""

    def transcribe(self, audio: bytes, mime: str, prompt: str | None = None) -> str:
        """Transcribe one audio payload or raise a typed transcription error.

        ``prompt`` is unstructured context (episode title, vocabulary) that helps
        the model spell proper nouns correctly.
        """
