from typing import Any

import httpx

from knowledge_assistant.infrastructure.telegram.client import TelegramClient


def test_telegram_client_parses_updates_and_sends_reply() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 12,
                            "message": {
                                "message_id": 4,
                                "chat": {"id": 8, "type": "private"},
                                "from": {"id": 9},
                                "text": "hello",
                            },
                        }
                    ],
                },
                request=request,
            )
        return httpx.Response(200, json={"ok": True, "result": {}}, request=request)

    client = TelegramClient(
        token="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    updates = client.get_updates(offset=10, timeout_seconds=2)
    client.send_message(chat_id=8, text="reply", reply_to_message_id=4)

    assert updates[0].message is not None
    assert updates[0].message.sender_id == 9
    sent: dict[str, Any] = __import__("json").loads(requests[1].content)
    assert sent["reply_parameters"]["message_id"] == 4


def test_telegram_client_splits_long_messages() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}}, request=request)

    client = TelegramClient(
        token="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.send_message(chat_id=8, text=("paragraph\n\n" * 900), reply_to_message_id=4)

    assert len(requests) >= 2
    payloads: list[dict[str, Any]] = [
        __import__("json").loads(request.content) for request in requests
    ]
    assert "reply_parameters" in payloads[0]
    assert all("reply_parameters" not in payload for payload in payloads[1:])
