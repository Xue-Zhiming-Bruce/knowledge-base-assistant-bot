"""Tempo MPP transport for Xquik's ordered X Article endpoint."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from knowledge_assistant.domain.sources import SourceFetchError
from knowledge_assistant.domain.x_articles import XArticleDocument
from knowledge_assistant.infrastructure.http.xquik_article_provider import (
    XquikArticlePayloadParser,
)

_POST_ID = re.compile(r"\A\d{15,20}\Z")
_XQUIK_ARTICLE_BASE_URL = "https://xquik.com/api/v1/x/articles"


class TempoCommandRunner(Protocol):
    """Run a bounded Tempo command without invoking a shell."""

    def __call__(
        self,
        command: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]: ...


def _run_tempo_command(
    command: Sequence[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


class TempoXquikArticleProvider:
    """Pay for one Xquik Article through a scoped Tempo wallet."""

    def __init__(
        self,
        *,
        max_spend_usdc: Decimal,
        tempo_request_command: str = "/usr/local/bin/tempo-request",
        max_response_bytes: int = 5_000_000,
        timeout_seconds: float = 30,
        runner: TempoCommandRunner | None = None,
        parser: XquikArticlePayloadParser | None = None,
    ) -> None:
        if max_spend_usdc <= 0 or max_spend_usdc > Decimal("1"):
            raise ValueError("max_spend_usdc must be greater than zero and at most 1")
        if not tempo_request_command.strip():
            raise ValueError("tempo_request_command must not be empty")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._max_spend = format(max_spend_usdc, "f")
        self._tempo_request_command = tempo_request_command
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._runner = runner or _run_tempo_command
        self._parser = parser or XquikArticlePayloadParser()

    def fetch_article(self, post_id: str) -> XArticleDocument:
        """Pay for and return exactly one validated, ordered X Article."""

        if _POST_ID.fullmatch(post_id) is None:
            raise SourceFetchError(
                "Xquik MPP refused an invalid numeric X post ID.",
                retryable=False,
            )
        url = f"{_XQUIK_ARTICLE_BASE_URL}/{post_id}"
        with tempfile.NamedTemporaryFile(
            prefix="knowledge-assistant-xquik-",
            suffix=".json",
        ) as output:
            output_path = Path(output.name)
        command = (
            self._tempo_request_command,
            "--silent",
            "--max-spend",
            self._max_spend,
            "--request",
            "GET",
            "--output",
            str(output_path),
            url,
        )
        try:
            try:
                result = self._runner(command, timeout=self._timeout_seconds)
            except FileNotFoundError as error:
                raise SourceFetchError(
                    "Tempo MPP is not installed in the worker container.",
                    retryable=False,
                ) from error
            except subprocess.TimeoutExpired as error:
                raise SourceFetchError(
                    "Tempo MPP timed out while requesting the Xquik Article.",
                    retryable=True,
                ) from error
            if result.returncode != 0:
                self._raise_command_failure(result)
            try:
                response_size = output_path.stat().st_size
            except OSError as error:
                raise SourceFetchError(
                    "Tempo MPP did not return an Xquik Article response.",
                    retryable=True,
                ) from error
            if response_size > self._max_response_bytes:
                raise SourceFetchError(
                    "Xquik Article response exceeds the configured size limit.",
                    retryable=False,
                )
            try:
                payload = json.loads(output_path.read_bytes())
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
                raise SourceFetchError(
                    "Tempo MPP returned an invalid Xquik Article response.",
                    retryable=False,
                ) from error
            return self._parser.parse(payload)
        finally:
            output_path.unlink(missing_ok=True)

    @staticmethod
    def _raise_command_failure(result: subprocess.CompletedProcess[bytes]) -> None:
        diagnostic = (result.stdout[-8_192:] + result.stderr[-8_192:]).decode(
            "utf-8",
            errors="replace",
        )
        normalized = diagnostic.lower()
        if any(
            marker in normalized
            for marker in (
                "max spend",
                "spending limit",
                "insufficient funds",
                "payment limit",
            )
        ):
            message = (
                "Tempo refused the Xquik payment because the configured spending cap "
                "or wallet balance was insufficient."
            )
            retryable = False
        elif any(
            marker in normalized
            for marker in ("no wallet", "not logged in", "access key", "wallet login")
        ):
            message = "Tempo MPP wallet authorization is missing or expired."
            retryable = False
        else:
            message = "Tempo MPP could not complete the Xquik Article request."
            retryable = True
        raise SourceFetchError(message, retryable=retryable)
