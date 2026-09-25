"""OpenAI speech-to-text transcription with ffmpeg segmentation for long audio."""

from __future__ import annotations

import io
import logging
import re
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

from knowledge_assistant.domain.podcasts import Transcript
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
# Nominal segment length. Real cut points are nudged onto pauses by
# ``_cut_points``; this is only the grid they are allowed to drift around.
_SEGMENT_SECONDS = 600
# How far a cut may drift from its grid mark, so segments stay 9m30s-10m30s.
_CUT_WINDOW_SECONDS = 30.0
# Audio quieter than this for at least this long counts as a pause. Length is
# the only evidence a gap is a sentence boundary rather than a breath, so the
# longest pause near each mark wins over merely the nearest one.
_PAUSE_NOISE_DB = -35
_MIN_PAUSE_SECONDS = 0.4
# A transient failure on segment N must not throw away segments 0..N-1: the
# job-level retry re-downloads the audio and transcribes from segment 0 again.
_SEGMENT_ATTEMPTS = 3
# Segments are independent requests. Three in flight keeps wall-clock near
# 1/3 of sequential without stampeding a marginal uplink.
_MAX_PARALLEL_SEGMENTS = 3


def _detected_language(response: Any) -> str | None:
    """First language the model reported, or None when it reported none.

    gpt-transcribe answers with ``languages: [{"code": "zh"}]``; whisper-1 with
    the default response format reports nothing at all.
    """
    if isinstance(response, dict):
        languages = response.get("languages")
    else:
        languages = getattr(response, "languages", None)
    for entry in languages or []:
        code = (
            entry.get("code")
            if isinstance(entry, dict)
            else getattr(entry, "code", None)
        )
        if code:
            return str(code).strip().lower()
    return None


def _format_marker(seconds: float) -> str:
    total = int(seconds)
    return f"[{total // 3600:02d}:{(total // 60) % 60:02d}]"


def _parse_media_duration(stderr: str) -> float | None:
    """Duration ffmpeg reports for its input, or None when it is unknown."""
    match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", stderr)
    if match is None:
        return None
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def _parse_pauses(stderr: str) -> tuple[tuple[float, float], ...]:
    """Quiet stretches from a ``silencedetect`` run, as (start, end) seconds."""
    starts = [float(value) for value in re.findall(r"silence_start: ([\d.]+)", stderr)]
    ends = [float(value) for value in re.findall(r"silence_end: ([\d.]+)", stderr)]
    return tuple(
        (start, end) for start, end in zip(starts, ends, strict=False) if end > start
    )


def _cut_points(
    pauses: tuple[tuple[float, float], ...],
    duration: float,
    *,
    segment_seconds: float,
    window_seconds: float,
) -> tuple[float, ...]:
    """Move each grid mark onto the longest pause within ``window_seconds`` of it.

    A mark with no usable pause nearby keeps its exact grid position, so audio
    with no detectable pauses splits exactly as the fixed grid would.
    """
    # Windows must not overlap, or a single pause can be claimed by two marks and
    # the later mark is then dropped by the ascending guard below.
    window = min(window_seconds, segment_seconds / 4)
    cuts: list[float] = []
    for mark in range(int(segment_seconds), int(duration), int(segment_seconds)):
        nearby = [
            pause
            for pause in pauses
            if abs((pause[0] + pause[1]) / 2 - mark) <= window
        ]
        if nearby:
            longest = max(nearby, key=lambda pause: pause[1] - pause[0])
            point = (longest[0] + longest[1]) / 2
        else:
            point = float(mark)
        # Strictly ascending: ffmpeg rejects any other ordering.
        if point > 0 and (not cuts or point > cuts[-1]):
            cuts.append(point)
    return tuple(cuts)
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

    def transcribe(
        self, audio: bytes, mime: str, prompt: str | None = None
    ) -> Transcript:
        filename = _SUPPORTED_EXTENSIONS.get(mime.split(";")[0].strip().lower())
        if filename is not None and len(audio) <= self._max_direct_bytes:
            return self._send(audio, filename, prompt)
        return self._transcribe_segmented(audio, prompt)

    def _transcribe_segmented(self, audio: bytes, prompt: str | None = None) -> Transcript:
        if self._ffmpeg_path is None:
            raise ExtractionError(
                "Audio is too large for a single transcription request and "
                "ffmpeg is not installed to split it."
            )
        with tempfile.TemporaryDirectory(prefix="ka-transcribe-") as tmp:
            work = Path(tmp)
            raw_input = work / "input"
            raw_input.write_bytes(audio)
            pauses, duration = self._pause_report(raw_input)
            cuts = (
                _cut_points(
                    pauses,
                    duration,
                    segment_seconds=self._segment_seconds,
                    window_seconds=_CUT_WINDOW_SECONDS,
                )
                if duration is not None
                else ()
            )
            split = (
                ("-segment_times", ",".join(f"{cut:.3f}" for cut in cuts))
                if cuts
                else ("-segment_time", str(self._segment_seconds))
            )
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
                *split,
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
            # Offsets come from the cut times we asked for, never from a naive
            # index * segment_seconds, which would be wrong for nudged cuts.
            offsets = (
                (0.0, *cuts)
                if cuts
                else tuple(
                    float(index * self._segment_seconds)
                    for index in range(len(parts))
                )
            )
            if len(offsets) != len(parts):
                logger.warning(
                    "transcription_segment_count planned=%s produced=%s",
                    len(offsets),
                    len(parts),
                )
            # map() yields in input order, so the transcript stays sequential
            # even though the requests run concurrently.
            with ThreadPoolExecutor(max_workers=self._max_parallel_segments) as pool:
                transcribed = list(
                    pool.map(
                        lambda pair: self._transcribe_part(pair[0], pair[1], prompt),
                        tuple(zip(offsets, parts, strict=False)),
                    )
                )
            return Transcript(
                text="\n\n".join(segment.text for segment in transcribed),
                # First segment that reported a language wins; they all carry the
                # same episode, and a disagreement is not worth reporting.
                language=next(
                    (segment.language for segment in transcribed if segment.language),
                    None,
                ),
            )

    def _pause_report(
        self, path: Path
    ) -> tuple[tuple[tuple[float, float], ...], float | None]:
        """Decode once to report pauses and the duration; writes no output file.

        A failure here is never fatal: the caller falls back to fixed-grid
        splitting, so silence detection can only improve a cut, never break one.
        """
        assert self._ffmpeg_path is not None
        try:
            result = subprocess.run(
                [
                    self._ffmpeg_path,
                    "-nostdin",
                    "-i",
                    str(path),
                    "-af",
                    f"silencedetect=noise={_PAUSE_NOISE_DB}dB:d={_MIN_PAUSE_SECONDS}",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                timeout=1800,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return (), None
        stderr = result.stderr.decode(errors="replace")
        return _parse_pauses(stderr), _parse_media_duration(stderr)

    def _transcribe_part(
        self, offset: float, part: Path, prompt: str | None = None
    ) -> Transcript:
        audio = part.read_bytes()
        logger.info(
            "transcription_segment offset=%.2f bytes=%s",
            offset,
            len(audio),
        )
        text = self._send_retrying(audio, part.name, prompt)
        return Transcript(
            text=f"{_format_marker(offset)} {text.text.strip()}",
            language=text.language,
        )

    def _send_retrying(
        self, audio: bytes, filename: str, prompt: str | None = None
    ) -> Transcript:
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

    def _send(
        self, audio: bytes, filename: str, prompt: str | None = None
    ) -> Transcript:
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
        return Transcript(text=str(text), language=_detected_language(response))
