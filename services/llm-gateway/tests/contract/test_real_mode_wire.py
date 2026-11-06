"""Real mode over a real socket, against a local stand-in for Anthropic's server.

FakeClaude replaces the HTTP layer, so these tests cover the code it skips: the upstream URL,
what is sent on the wire, and how a live connection is read and closed.
"""

import json
import threading
from collections.abc import Iterator
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from llm_gateway.app import create_app
from llm_gateway.ledger import InMemoryLedger
from llm_gateway.settings import Settings

from ..conftest import AUTH, TOKEN, body

USAGE = {"input_tokens": 1000, "output_tokens": 100}
EVENTS: list[dict[str, Any]] = [
    {"type": "message_start", "message": {"usage": {"input_tokens": 1000, "output_tokens": 1}}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 100},
    },
    {"type": "message_stop"},
]
# Sonnet: 1000 input at $2 per million, plus 100 output at $10 per million.
EXPECTED_COST = Decimal("0.003")


class Server:
    url: str
    seen: list[dict[str, str]]


@pytest.fixture
def anthropic_server() -> Iterator[Server]:
    seen: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append({key.lower(): value for key, value in self.headers.items()})
            if payload.get("stream"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for event in EVENTS:
                    self.wfile.write(
                        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
                    )
                    self.wfile.flush()
                return
            data = json.dumps({"id": "m", "content": [], "usage": USAGE}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result = Server()
    result.url = f"http://127.0.0.1:{server.server_port}"
    result.seen = seen
    try:
        yield result
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def real_client(server: Server, ledger: InMemoryLedger) -> TestClient:
    settings = Settings(
        llm_gateway_mode="real",
        llm_gateway_token=SecretStr(TOKEN),
        anthropic_api_key=SecretStr("real-claude-key"),
        anthropic_base_url=server.url,
        database_url=SecretStr("postgresql+asyncpg://unused"),
    )
    return TestClient(create_app(settings, ledger=ledger))


@pytest.mark.enable_socket
def test_normal_call_uses_the_real_key_and_is_costed(anthropic_server: Server) -> None:
    ledger = InMemoryLedger()

    response = real_client(anthropic_server, ledger).post("/v1/messages", json=body(), headers=AUTH)

    assert response.status_code == 200
    assert Decimal(response.headers["x-scout-cost-usd"]) == EXPECTED_COST
    sent = anthropic_server.seen[0]
    assert sent["x-api-key"] == "real-claude-key"
    assert "authorization" not in sent
    assert TOKEN not in str(sent)
    assert ledger.records[0].cost_usd == EXPECTED_COST


@pytest.mark.enable_socket
def test_stream_arrives_whole_and_is_costed(anthropic_server: Server) -> None:
    ledger = InMemoryLedger()

    response = real_client(anthropic_server, ledger).post(
        "/v1/messages", json=body(stream=True), headers=AUTH
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: message_stop" in response.text
    assert anthropic_server.seen[0]["accept-encoding"] == "identity"
    assert ledger.records[0].cost_usd == EXPECTED_COST
    assert not ledger.records[0].estimated
