"""A fake Anthropic API on 127.0.0.1, so wire tests see the real HTTP request."""

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

MESSAGE: dict[str, Any] = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5-5",
    "content": [
        {"type": "text", "text": "Checking KEV."},
        {
            "type": "tool_use",
            "id": "toolu_test",
            "name": "get_kev",
            "input": {"cve": "CVE-2021-44228"},
        },
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 3,
    },
}

STREAM_EVENTS: list[dict[str, Any]] = [
    {
        "type": "message_start",
        "message": {**MESSAGE, "content": [], "stop_reason": None},
    },
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Patch first."},
    },
    {"type": "content_block_stop", "index": 0},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 2},
    },
    {"type": "message_stop"},
]


@dataclass
class RecordedRequest:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class FakeAnthropicApi:
    url: str
    requests: list[RecordedRequest] = field(default_factory=list[RecordedRequest])

    @property
    def last(self) -> RecordedRequest:
        return self.requests[-1]


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAnthropicApi]:
    # A developer's proxy settings must not capture calls to the fake API.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    recorded: list[RecordedRequest] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            calls = len(recorded)
            self._send_json(
                {
                    "date": "2026-11-05",
                    "mode": "fake",
                    "limit_usd": "15",
                    "spent_usd": str(calls * 0.01),
                    "remaining_usd": "14",
                    "requests": calls,
                }
            )

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(length))
            headers = {key.lower(): value for key, value in self.headers.items()}
            recorded.append(RecordedRequest(self.path, headers, body))
            if body.get("stream"):
                self._send_stream()
            else:
                self._send_json(MESSAGE)

        def _send_json(self, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_stream(self) -> None:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for event in STREAM_EVENTS:
                line = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                self.wfile.write(line.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield FakeAnthropicApi(f"http://127.0.0.1:{server.server_port}", recorded)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
