"""Builds llm-gateway around FakeClaude and an in-memory ledger, with no network."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from llm_gateway.app import create_app
from llm_gateway.ledger import InMemoryLedger
from llm_gateway.settings import Settings
from pydantic import SecretStr
from scout_testkit.fake_claude import FakeClaude, Step

TOKEN = "internal-token"
AUTH = {"authorization": f"Bearer {TOKEN}"}
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def body(**overrides: Any) -> dict[str, Any]:
    return {
        "model": "claude-sonnet-5-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hello"}],
        **overrides,
    }


@dataclass
class Harness:
    client: TestClient
    fake: FakeClaude
    ledger: InMemoryLedger
    sleeps: list[float] = field(default_factory=list[float])

    def post(self, **overrides: Any) -> Any:
        return self.client.post("/v1/messages", json=body(**overrides), headers=AUTH)


@pytest.fixture
def harness() -> Callable[..., Harness]:
    def build(*steps: Step, limit: str = "15", ledger: InMemoryLedger | None = None) -> Harness:
        store = ledger or InMemoryLedger()
        fake = FakeClaude(list(steps))
        sleeps: list[float] = []

        async def record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        settings = Settings(
            llm_gateway_mode="fake",
            llm_gateway_token=SecretStr(TOKEN),
            anthropic_api_key=SecretStr("real-claude-key"),
            llm_daily_limit_usd=Decimal(limit),
            database_url=None,
        )
        app = create_app(
            settings, transport=fake, ledger=store, sleep=record_sleep, clock=lambda: NOW
        )
        return Harness(TestClient(app), fake, store, sleeps)

    return build
