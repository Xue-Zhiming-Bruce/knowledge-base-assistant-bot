from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest

from knowledge_assistant.domain.sources import SourceFetchError
from knowledge_assistant.infrastructure.http.tempo_xquik_article_provider import (
    TempoXquikArticleProvider,
)


def test_tempo_xquik_pays_with_cap_and_preserves_exact_block_order() -> None:
    commands: list[tuple[str, ...]] = []
    output_paths: list[Path] = []
    payload = {
        "article": {
            "title": "Ordered rich content",
            "contents": [
                {"type": "header-two", "text": "First heading"},
                {"type": "paragraph", "text": "Before the code."},
                {"type": "code-block", "text": "print('in place')"},
                {
                    "type": "media",
                    "url": "https://pbs.twimg.com/media/diagram.png",
                    "altText": "Architecture diagram",
                },
                {"type": "blockquote", "text": "After the figure."},
                {"type": "divider"},
            ],
        }
    }

    def runner(
        command: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        assert timeout == 12
        captured = tuple(command)
        commands.append(captured)
        output_path = Path(captured[captured.index("--output") + 1])
        output_paths.append(output_path)
        output_path.write_text(json.dumps(payload))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    provider = TempoXquikArticleProvider(
        max_spend_usdc=Decimal("0.001"),
        tempo_request_command="/test/tempo-request",
        timeout_seconds=12,
        runner=runner,
    )

    article = provider.fetch_article("2033891852621840387")

    assert tuple(block.kind for block in article.blocks) == (
        "header-two",
        "paragraph",
        "code-block",
        "media",
        "blockquote",
        "divider",
    )
    assert article.blocks[2].text == "print('in place')"
    assert article.blocks[3].url == "https://pbs.twimg.com/media/diagram.png"
    assert commands[0][:7] == (
        "/test/tempo-request",
        "--silent",
        "--max-spend",
        "0.001",
        "--request",
        "GET",
        "--output",
    )
    assert commands[0][-1] == ("https://xquik.com/api/v1/x/articles/2033891852621840387")
    assert all(not path.exists() for path in output_paths)


def test_tempo_xquik_rejects_invalid_id_before_running_payment_command() -> None:
    called = False

    def runner(
        command: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    provider = TempoXquikArticleProvider(
        max_spend_usdc=Decimal("0.001"),
        runner=runner,
    )

    with pytest.raises(SourceFetchError, match="invalid numeric"):
        provider.fetch_article("123;evil.example")

    assert called is False


@pytest.mark.parametrize(
    ("diagnostic", "message", "retryable"),
    [
        (b"max spend exceeded", "spending cap", False),
        (b"No wallet configured; run wallet login", "authorization", False),
        (b"upstream transport closed", "could not complete", True),
    ],
)
def test_tempo_xquik_classifies_command_failures_without_leaking_output(
    diagnostic: bytes,
    message: str,
    retryable: bool,
) -> None:
    def runner(
        command: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=diagnostic)

    provider = TempoXquikArticleProvider(
        max_spend_usdc=Decimal("0.001"),
        runner=runner,
    )

    with pytest.raises(SourceFetchError, match=message) as caught:
        provider.fetch_article("2033891852621840387")

    assert caught.value.retryable is retryable
    assert diagnostic.decode() not in str(caught.value)


def test_tempo_xquik_rejects_lossy_payload_after_payment() -> None:
    def runner(
        command: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "article": {
                        "title": "Unsupported",
                        "contents": [{"type": "interactive-poll", "text": "Vote"}],
                    }
                }
            )
        )
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    provider = TempoXquikArticleProvider(
        max_spend_usdc=Decimal("0.001"),
        runner=runner,
    )

    with pytest.raises(SourceFetchError, match="cannot be saved losslessly"):
        provider.fetch_article("2033891852621840387")


def test_tempo_xquik_fails_on_oversized_response() -> None:
    def runner(
        command: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_bytes(b"{}" * 10)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    provider = TempoXquikArticleProvider(
        max_spend_usdc=Decimal("0.001"),
        max_response_bytes=10,
        runner=runner,
    )

    with pytest.raises(SourceFetchError, match="size limit"):
        provider.fetch_article("2033891852621840387")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_spend_usdc": Decimal("0")}, "max_spend_usdc"),
        (
            {"max_spend_usdc": Decimal("0.001"), "tempo_request_command": " "},
            "tempo_request_command",
        ),
        (
            {"max_spend_usdc": Decimal("0.001"), "max_response_bytes": 0},
            "max_response_bytes",
        ),
        (
            {"max_spend_usdc": Decimal("0.001"), "timeout_seconds": 0},
            "timeout_seconds",
        ),
    ],
)
def test_tempo_xquik_rejects_unsafe_configuration(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        TempoXquikArticleProvider(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("failure", "message", "retryable"),
    [
        (FileNotFoundError(), "not installed", False),
        (subprocess.TimeoutExpired("tempo-request", 30), "timed out", True),
    ],
)
def test_tempo_xquik_classifies_process_failures(
    failure: Exception,
    message: str,
    retryable: bool,
) -> None:
    def runner(
        command: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        raise failure

    provider = TempoXquikArticleProvider(
        max_spend_usdc=Decimal("0.001"),
        runner=runner,
    )

    with pytest.raises(SourceFetchError, match=message) as caught:
        provider.fetch_article("2033891852621840387")

    assert caught.value.retryable is retryable


@pytest.mark.parametrize(
    ("body", "message", "retryable"),
    [
        (b"not-json", "invalid", False),
        (json.dumps({"error": "article_not_found"}).encode(), "could not find", False),
        (json.dumps({"error": "x_api_unavailable"}).encode(), "unavailable", True),
        (json.dumps({"error": "rate_limit_exceeded"}).encode(), "rate limit", True),
    ],
)
def test_tempo_xquik_classifies_response_failures(
    body: bytes,
    message: str,
    retryable: bool,
) -> None:
    def runner(
        command: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_bytes(body)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    provider = TempoXquikArticleProvider(
        max_spend_usdc=Decimal("0.001"),
        runner=runner,
    )

    with pytest.raises(SourceFetchError, match=message) as caught:
        provider.fetch_article("2033891852621840387")

    assert caught.value.retryable is retryable


def test_tempo_xquik_fails_when_command_returns_no_body() -> None:
    provider = TempoXquikArticleProvider(
        max_spend_usdc=Decimal("0.001"),
        runner=lambda command, timeout: subprocess.CompletedProcess(
            command,
            0,
            stdout=b"",
            stderr=b"",
        ),
    )

    with pytest.raises(SourceFetchError, match="did not return") as caught:
        provider.fetch_article("2033891852621840387")

    assert caught.value.retryable is True
