"""OpenAI speech-to-text transcription with ffmpeg segmentation for long audio."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from knowledge_assistant.domain.sources import ExtractionError

logger = logging.getLogger(__name__)

# OpenAI audio input limit is 25MB; stay safely below it.
_MAX_DIRECT_BYTES = 20 * 1024 * 1024
_SUPPORTED_EXTENSIONS = {
    "audio/mpeg": "audio.mp3",
    "audio/mp3": "audio.mp3",
    "audio/mp4": "audio.m4a",
    "audio/x-m4a": "audio.m4a",
    "audio/m4a": "audio.m4a",
    "audio/wav": "audio.wav",
    "audio/x-wav": "audio.wav",
    "audio/webm": "audio.webm",
}
# ponytail: fixed 10-minute segments cut mid-word at boundaries occasionally.
# Upgrade to silence-aware splitting (silencedetect) if boundary loss matters.
_SEGMENT_SECONDS = 600
# A transient failure on segment N must not throw away segments 0..N-1: the
# job-level retry re-downloads the audio and transcribes from segment 0 again.
_SEGMENT_ATTEMPTS = 3
# Segments are independent requests. Three in flight keeps wall-clock near
# 1/3 of sequential without stampeding a marginal uplink.
_MAX_PARALLEL_SEGMENTS = 3
# Retrying a malformed-upload 400 is deliberate: an interrupted multipart body
# surfaces as "something went wrong reading your request", not as a network error.
_RETRYABLE = (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)


class OpenAITranscriber:
    """Transcribe audio via the OpenAI transcriptions API."""

    def __init__(
        self,
        *,
        client: Any,
        model: str = "gpt-transcribe",
        max_direct_bytes: int = _MAX_DIRECT_BYTES,
        segment_seconds: int = _SEGMENT_SECONDS,
        ffmpeg_path: str | None = None,
        retry_delay_seconds: float = 5.0,
        max_parallel_segments: int = _MAX_PARALLEL_SEGMENTS,
    ) -> None:
        self._client = client
        self.model = model
        self._max_direct_bytes = max_direct_bytes
        self._segment_seconds = segment_seconds
        self._ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg")
        self._retry_delay_seconds = retry_delay_seconds
        self._max_parallel_segments = max(1, max_parallel_segments)

    def transcribe(self, audio: bytes, mime: str, prompt: str | None = None) -> str:
        filename = _SUPPORTED_EXTENSIONS.get(mime.split(";")[0].strip().lower())
        if filename is not None and len(audio) <= self._max_direct_bytes:
            return self._send(audio, filename, prompt)
        return self._transcribe_segmented(audio, prompt)

    def _transcribe_segmented(self, audio: bytes, prompt: str | None = None) -> str:
        if self._ffmpeg_path is None:
            raise ExtractionError(
                "Audio is too large for a single transcription request and "
                "ffmpeg is not installed to split it."
            )
        with tempfile.TemporaryDirectory(prefix="ka-transcribe-") as tmp:
            work = Path(tmp)
            raw_input = work / "input"
            raw_input.write_bytes(audio)
            command = [
                self._ffmpeg_path,
                "-nostdin",
                "-y",
                "-i",
                str(raw_input),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "64k",
                "-f",
                "segment",
                "-segment_time",
                str(self._segment_seconds),
                "-reset_timestamps",
                "1",
                str(work / "seg%04d.mp3"),
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=1800,
                check=False,
            )
            if result.returncode != 0:
                raise ExtractionError(
                    f"ffmpeg audio segmentation failed: "
                    f"{result.stderr.decode(errors='replace')[-300:]}"
                )
            parts = sorted(work.glob("seg*.mp3"))
            if not parts:
                raise ExtractionError("ffmpeg produced no audio segments.")
            # map() yields in input order, so the transcript stays sequential
            # even though the requests run concurrently.
            with ThreadPoolExecutor(max_workers=self._max_parallel_segments) as pool:
                texts = list(
                    pool.map(
                        lambda item: self._transcribe_part(item[0], item[1], prompt),
                        enumerate(parts),
                    )
                )
            return "\n\n".join(texts)

    def _transcribe_part(self, index: int, part: Path, prompt: str | None = None) -> str:
        audio = part.read_bytes()
        logger.info(
            "transcription_segment index=%s bytes=%s",
            index,
            len(audio),
        )
        text = self._send_retrying(audio, part.name, prompt)
        marker = index * self._segment_seconds
        return f"[{marker // 3600:02d}:{(marker // 60) % 60:02d}] {text.strip()}"

    def _send_retrying(
        self, audio: bytes, filename: str, prompt: str | None = None
    ) -> str:
        """Send one segment, retrying it in place before failing the whole job."""
        delay = self._retry_delay_seconds
        for attempt in range(_SEGMENT_ATTEMPTS):
            try:
                return self._send(audio, filename, prompt)
            except _RETRYABLE:
                if attempt + 1 == _SEGMENT_ATTEMPTS:
                    raise
                time.sleep(delay)
                delay *= 4
        raise AssertionError("unreachable")

    def _send(self, audio: bytes, filename: str, prompt: str | None = None) -> str:
        # OpenAI SDK errors propagate unwrapped: RateLimitError/timeout remain
        # retryable in the worker's failure classifier. An empty prompt is
        # omitted rather than sent as "", which some models reject.
        response = self._client.audio.transcriptions.create(
            model=self.model,
            file=(filename, io.BytesIO(audio)),
            **({"prompt": prompt} if prompt else {}),
        )
        text = getattr(response, "text", None)
        if not text or not str(text).strip():
            raise ExtractionError("Transcription returned no text.")
        return str(text)
