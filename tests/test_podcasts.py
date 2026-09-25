"""Podcast ingestion: classification, resolvers, transcription, service."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from openai import InternalServerError

from knowledge_assistant.application.podcasts import PodcastService
from knowledge_assistant.domain.documents import DocumentId, SourceType
from knowledge_assistant.domain.podcasts import PodcastEpisode
from knowledge_assistant.domain.sources import (
    ExtractionError,
    SourceClassifier,
    UnsupportedSourceError,
)
from knowledge_assistant.infrastructure.http.podcast_resolver import (
    PodcastEpisodeResolver,
)
from knowledge_assistant.infrastructure.openai.transcription import OpenAITranscriber


class FakeTranscriber:
    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.prompts: list[str | None] = []

    def transcribe(self, audio: bytes, mime: str, prompt: str | None = None) -> str:
        self.calls.append(audio)
        self.prompts.append(prompt)
        return f"transcript({len(audio)} bytes, {mime})"


def _resolver_with(responder: Any) -> PodcastEpisodeResolver:
    return PodcastEpisodeResolver(
        client=httpx.Client(transport=httpx.MockTransport(responder))
    )


class TestClassification:
    def test_xiaoyuzhou_episode_is_podcast(self) -> None:
        source = SourceClassifier().classify(
            "https://www.xiaoyuzhoufm.com/episode/6740632C8D1233FB0D3A9CEA?s=1"
        )
        assert source.provider.value == "podcast"
        assert source.source_type is SourceType.PODCAST
        assert source.canonical_url == (
            "https://www.xiaoyuzhoufm.com/episode/6740632c8d1233fb0d3a9cea"
        )
        assert source.normalized_source_key == "podcast:xiaoyuzhou:6740632c8d1233fb0d3a9cea"

    def test_apple_episode_is_podcast(self) -> None:
        source = SourceClassifier().classify(
            "https://podcasts.apple.com/us/podcast/show/id1629296562?i=1000665216512"
        )
        assert source.source_type is SourceType.PODCAST
        assert source.normalized_source_key == "podcast:apple:1000665216512"

    def test_apple_show_link_is_rejected(self) -> None:
        with pytest.raises(UnsupportedSourceError):
            SourceClassifier().classify(
                "https://podcasts.apple.com/us/podcast/show/id1629296562"
            )

    def test_xiaoyuzhou_podcast_link_is_rejected(self) -> None:
        with pytest.raises(UnsupportedSourceError):
            SourceClassifier().classify("https://www.xiaoyuzhoufm.com/podcast/abc123")


class TestXiaoyuzhouResolver:
    def test_resolves_episode_metadata_and_audio(self) -> None:
        episode_json = json.dumps(
            {
                "props": {
                    "pageProps": {
                        "episode": {
                            "title": "74. AI硬件创新浪潮",
                            "duration": 767,
                            "pubDate": "2024-11-22T10:56:57.525Z",
                            "shownotes": "<p>show <b>notes</b></p>",
                            "enclosure": {"url": "https://media.xyzcdn.net/x.m4a"},
                            "podcast": {"pid": "p1", "title": "AI产品观察"},
                        }
                    }
                }
            }
        )
        page = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            f"{episode_json}</script></html>"
        )
        resolver = _resolver_with(lambda request: httpx.Response(200, text=page))
        episode = resolver.resolve_xiaoyuzhou(
            "https://www.xiaoyuzhoufm.com/episode/6740632c8d1233fb0d3a9cea"
        )
        assert episode.audio_url == "https://media.xyzcdn.net/x.m4a"
        assert episode.title == "74. AI硬件创新浪潮"
        assert episode.show == "AI产品观察"
        assert episode.duration_seconds == 767
        assert episode.published_at == datetime(2024, 11, 22, 10, 56, 57, 525000, tzinfo=UTC)
        assert episode.shownotes_markdown == "show **notes**"

    def test_premium_episode_without_audio_raises(self) -> None:
        page = (
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"episode": {"title": "paid"}}}})
            + "</script>"
        )
        resolver = _resolver_with(lambda request: httpx.Response(200, text=page))
        with pytest.raises(Exception, match="publicly accessible audio"):
            resolver.resolve_xiaoyuzhou("https://www.xiaoyuzhoufm.com/episode/x")


class TestAppleResolver:
    def test_resolves_stream_url_from_page_json(self) -> None:
        offer_json = json.dumps(
            {
                "shelves": [
                    {
                        "items": [
                            {
                                "contextAction": {
                                    "episodeOffer": {
                                        "contentId": "1000665216512",
                                        "title": "EP04-離職前往日本",
                                        "releaseDate": "2024-08-14T04:08:25Z",
                                        "showOffer": {"title": "阿哲筆記"},
                                        "currentMediaEnclosure": {
                                            "streamUrl": "https://cdn.example.net/full.mp3",
                                            "duration": 977,
                                        },
                                    }
                                }
                            }
                        ]
                    }
                ]
            }
        )
        page = f"<html><script>var d={offer_json};</script></html>"
        resolver = _resolver_with(lambda request: httpx.Response(200, text=page))
        episode = resolver.resolve_apple(
            "https://podcasts.apple.com/us/podcast/show/id1?i=1000665216512"
        )
        assert episode.audio_url == "https://cdn.example.net/full.mp3"
        assert episode.title == "EP04-離職前往日本"
        assert episode.show == "阿哲筆記"
        assert episode.duration_seconds == 977


class TestSubstackProbe:
    def test_probe_detects_podcast_post(self) -> None:
        post = {
            "title": "Members Only #344",
            "podcast_url": "https://api.substack.com/api/v1/audio/upload/abc/src",
            "podcast_duration": 1073.0,
            "post_date": "2026-09-18T11:03:48.179Z",
            "description": "Why Tyler Quit",
        }
        resolver = _resolver_with(
            lambda request: (
                httpx.Response(200, json=post)
                if "/api/v1/posts/" in str(request.url)
                else httpx.Response(404)
            )
        )
        source = SourceClassifier().classify("https://writer.substack.com/p/ep-344")
        episode = resolver.probe_substack(source)
        assert episode is not None
        assert episode.audio_url == "https://api.substack.com/api/v1/audio/upload/abc/src"
        assert episode.duration_seconds == 1073

    def test_probe_returns_none_for_plain_article(self) -> None:
        resolver = _resolver_with(
            lambda request: httpx.Response(200, json={"title": "post", "podcast_url": None})
        )
        source = SourceClassifier().classify("https://writer.substack.com/p/post")
        assert resolver.probe_substack(source) is None


class TestOpenAITranscriber:
    def test_small_supported_audio_sends_directly(self) -> None:
        calls: list[tuple[str, object]] = []

        class FakeClient:
            class audio:  # noqa: N801
                class transcriptions:  # noqa: N801
                    @staticmethod
                    def create(*, model: str, file: object) -> object:
                        calls.append((model, file))
                        return type("R", (), {"text": "hello"})()

        transcriber = OpenAITranscriber(
            client=cast(Any, FakeClient()), model="whisper-1", ffmpeg_path=None
        )
        text = transcriber.transcribe(b"tiny", "audio/mp4")
        assert text == "hello"
        assert calls[0][0] == "whisper-1"

    def test_unsupported_mime_requires_ffmpeg(self) -> None:
        transcriber = OpenAITranscriber(client=cast(Any, object()), ffmpeg_path=None)
        with pytest.raises(ExtractionError, match="ffmpeg"):
            transcriber.transcribe(b"x" * 10, "audio/ogg")

    def test_prompt_is_forwarded_and_omitted_when_absent(self) -> None:
        seen: list[dict[str, Any]] = []

        class FakeClient:
            class audio:  # noqa: N801
                class transcriptions:  # noqa: N801
                    @staticmethod
                    def create(**kwargs: Any) -> object:
                        seen.append(kwargs)
                        return type("R", (), {"text": "hello"})()

        transcriber = OpenAITranscriber(
            client=cast(Any, FakeClient()), model="gpt-transcribe", ffmpeg_path=None
        )
        transcriber.transcribe(b"tiny", "audio/mp4", "BitMEX 永续合约")
        assert seen[0]["prompt"] == "BitMEX 永续合约"
        assert seen[0]["model"] == "gpt-transcribe"

        # An absent prompt is omitted rather than sent as "".
        transcriber.transcribe(b"tiny", "audio/mp4")
        assert "prompt" not in seen[1]

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
    def test_segment_retries_in_place_before_failing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts: list[str] = []

        class FakeClient:
            class audio:  # noqa: N801
                class transcriptions:  # noqa: N801
                    @staticmethod
                    def create(*, model: str, file: object) -> object:
                        filename, _ = cast("tuple[str, Any]", file)
                        attempts.append(filename)
                        if attempts.count(filename) == 1:
                            raise InternalServerError(
                                "Error code: 500",
                                response=httpx.Response(500, request=httpx.Request("POST", "https://x")),
                                body=None,
                            )
                        return type("R", (), {"text": "ok"})()

        transcriber = OpenAITranscriber(
            client=cast(Any, FakeClient()),
            segment_seconds=1,
            max_direct_bytes=0,
            retry_delay_seconds=0,
        )
        monkeypatch.setattr("time.sleep", lambda _: None)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tone.wav"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    str(wav),
                ],
                check=True,
                capture_output=True,
            )
            text = transcriber.transcribe(wav.read_bytes(), "audio/wav")

        # ffmpeg's segment count is frame-boundary dependent, so derive it from
        # the calls rather than assuming how many the 2s tone splits into.
        segments = sorted(set(attempts))
        assert len(segments) >= 2
        assert [attempts.count(name) for name in segments] == [2] * len(segments)
        assert text.count("ok") == len(segments)

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
    def test_segmentation_produces_timestamped_chunks(self) -> None:
        captured: list[bytes] = []

        class FakeClient:
            class audio:  # noqa: N801
                class transcriptions:  # noqa: N801
                    @staticmethod
                    def create(*, model: str, file: object) -> object:
                        filename, payload = cast(
                            "tuple[str, Any]", file
                        )
                        captured.append(payload.read())
                        return type("R", (), {"text": f"seg {filename}"})()

        transcriber = OpenAITranscriber(
            client=cast(Any, FakeClient()),
            segment_seconds=60,
            max_direct_bytes=0,  # force the segmented path even for tiny input
        )
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tone.wav"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=120",
                    str(wav),
                ],
                check=True,
                capture_output=True,
            )
            text = transcriber.transcribe(wav.read_bytes(), "audio/wav")

        markers = [line.split()[0] for line in text.strip().split("\n\n")]
        assert len(markers) >= 2
        assert markers[0] == "[00:00]"
        assert markers[1] == "[00:01]"

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
    def test_segments_are_transcribed_concurrently(self) -> None:
        lock = threading.Lock()
        in_flight = 0
        peak = 0

        class FakeClient:
            class audio:  # noqa: N801
                class transcriptions:  # noqa: N801
                    @staticmethod
                    def create(*, model: str, file: object) -> object:
                        nonlocal in_flight, peak
                        with lock:
                            in_flight += 1
                            peak = max(peak, in_flight)
                        time.sleep(0.2)  # hold the slot so overlap is observable
                        with lock:
                            in_flight -= 1
                        filename, _ = cast("tuple[str, Any]", file)
                        return type("R", (), {"text": filename})()

        transcriber = OpenAITranscriber(
            client=cast(Any, FakeClient()),
            segment_seconds=60,
            max_direct_bytes=0,
            max_parallel_segments=3,
        )
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tone.wav"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=300",
                    str(wav),
                ],
                check=True,
                capture_output=True,
            )
            transcriber.transcribe(wav.read_bytes(), "audio/wav")

        assert peak > 1, "segments were transcribed one at a time"
        assert peak <= 3


class TestPodcastService:
    def test_transcribe_builds_transcript_article(self) -> None:
        episode = PodcastEpisode(
            title="Ep 1",
            audio_url="https://cdn.example.net/a.mp3",
            show="Show",
            published_at=datetime(2024, 1, 1, tzinfo=UTC),
            duration_seconds=600,
            shownotes_markdown="notes",
        )

        class FakeDownloader:
            def download(self, url: str) -> tuple[bytes, str]:
                return b"AUDIO", "audio/mpeg"

        fake_transcriber = FakeTranscriber()
        service = PodcastService(
            transcriber=cast(Any, fake_transcriber),
            transcriber_model="whisper-1",
            downloader=cast(Any, FakeDownloader()),
        )
        article = service.transcribe(episode, "https://example.com/ep1")
        assert article.title == "Ep 1"
        assert article.authors == ("Show",)
        assert "- Podcast: Show" in article.markdown
        assert "- Duration: 10m 00s" in article.markdown
        assert "## Show notes" in article.markdown
        assert "transcript(5 bytes, audio/mpeg)" in article.markdown
        assert service.extractor_label == "podcast-transcript-whisper-1"
        # The episode title is what keeps BitMEX/Bybit spelled correctly.
        assert fake_transcriber.prompts == ["Ep 1 Show"]

    def test_transcription_prompt_is_collapsed_and_bounded(self) -> None:
        episode = PodcastEpisode(
            title="Ep\n  1\t" + "x" * 500,
            audio_url="https://cdn.example.net/a.mp3",
            show="Show",
        )
        prompt = PodcastService._prompt(episode)
        assert prompt is not None
        assert len(prompt) == 400
        assert "\n" not in prompt
        assert "\t" not in prompt
        assert "  " not in prompt

    def test_transcription_prompt_is_none_without_title_or_show(self) -> None:
        episode = PodcastEpisode(title="", audio_url="https://cdn.example.net/a.mp3")
        assert PodcastService._prompt(episode) is None


class TestVaultPath:
    def test_podcasts_use_podcasts_root_and_platform_folder(self) -> None:
        from knowledge_assistant.application.worker import IngestionWorker
        from knowledge_assistant.infrastructure.postgres.ingestion_repository import (
            ClaimedJob,
        )

        source = SourceClassifier().classify(
            "https://www.xiaoyuzhoufm.com/episode/6740632c8d1233fb0d3a9cea"
        )
        assert IngestionWorker._vault_folder(source, podcast=True) == "xiaoyuzhou"
        assert (
            IngestionWorker._vault_folder(source, podcast=False) == source.provider.value
        )
        path = IngestionWorker._vault_path(
            "xiaoyuzhou",
            "74. AI硬件",
            DocumentId("doc_0123456789abcdef0123456789abcdef"),
            topic=None,
            root="Podcasts",
        )
        assert path.parts[:3] == ("Podcasts", "xiaoyuzhou", "_Pending")

        # Substack podcast keeps its platform folder under Podcasts/.
        substack = SourceClassifier().classify("https://writer.substack.com/p/ep-1")
        job = ClaimedJob(
            job_id=uuid.uuid4(),
            source_url=substack.canonical_url,
            normalized_source_key=substack.normalized_source_key,
            source_type="article",
            source_provider="substack",
            attempt_count=1,
        )
        assert (
            IngestionWorker._vault_folder(substack, podcast=True) == "substack"
        )
        assert job.source_provider == "substack"
