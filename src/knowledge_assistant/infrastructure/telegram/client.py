"""Small, typed Telegram Bot API client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class TelegramApiError(RuntimeError):
    """Telegram could not accept or serve a Bot API request."""


@dataclass(frozen=True, slots=True)
class TelegramMessage:
    message_id: int
    chat_id: int
    chat_type: str
    sender_id: int
    text: str
    reply_to_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class TelegramUpdate:
    update_id: int
    message: TelegramMessage | None


class TelegramClient:
    """Call only the Bot API operations required by the thin client."""

    def __init__(
        self,
        *,
        token: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._client = client or httpx.Client(timeout=httpx.Timeout(40))
        self._base_url = f"https://api.telegram.org/bot{token}"

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout_seconds: int,
    ) -> tuple[TelegramUpdate, ...]:
        parameters: dict[str, Any] = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            parameters["offset"] = offset
        result = self._request("getUpdates", parameters)
        if not isinstance(result, list):
            raise TelegramApiError("Telegram getUpdates returned an invalid result")
        return tuple(self._parse_update(item) for item in result)

    def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> int | None:
        sent_message_id: int | None = None
        for index, chunk in enumerate(self._message_chunks(text)):
            parameters: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if index == 0 and reply_to_message_id is not None:
                parameters["reply_parameters"] = {
                    "message_id": reply_to_message_id,
                    "allow_sending_without_reply": True,
                }
            result = self._request("sendMessage", parameters)
            if index == 0 and isinstance(result, dict):
                message_id = result.get("message_id")
                if isinstance(message_id, int):
                    sent_message_id = message_id
        return sent_message_id

    def _request(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = self._client.post(f"{self._base_url}/{method}", json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise TelegramApiError(f"Telegram {method} request failed") from error
        if not body.get("ok"):
            description = str(body.get("description", "unknown Telegram API error"))
            raise TelegramApiError(f"Telegram {method} rejected the request: {description}")
        return body.get("result")

    @staticmethod
    def _parse_update(payload: dict[str, Any]) -> TelegramUpdate:
        raw_message = payload.get("message")
        message = None
        if isinstance(raw_message, dict):
            chat = raw_message.get("chat", {})
            sender = raw_message.get("from", {})
            text = raw_message.get("text")
            if isinstance(chat, dict) and isinstance(sender, dict) and isinstance(text, str):
                reply = raw_message.get("reply_to_message")
                reply_to_message_id = None
                if isinstance(reply, dict):
                    reply_id = reply.get("message_id")
                    if isinstance(reply_id, int):
                        reply_to_message_id = reply_id
                message = TelegramMessage(
                    message_id=int(raw_message["message_id"]),
                    chat_id=int(chat["id"]),
                    chat_type=str(chat.get("type", "")),
                    sender_id=int(sender["id"]),
                    text=text,
                    reply_to_message_id=reply_to_message_id,
                )
        return TelegramUpdate(update_id=int(payload["update_id"]), message=message)

    @staticmethod
    def _message_chunks(text: str, *, limit: int = 4_000) -> tuple[str, ...]:
        if len(text) <= limit:
            return (text,)
        chunks: list[str] = []
        remaining = text
        while remaining:
            split_at = remaining.rfind("\n\n", 0, limit + 1)
            if split_at < limit // 2:
                split_at = remaining.rfind("\n", 0, limit + 1)
            if split_at < limit // 2:
                split_at = min(limit, len(remaining))
            chunk = remaining[:split_at].strip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_at:].strip()
        return tuple(chunks)
