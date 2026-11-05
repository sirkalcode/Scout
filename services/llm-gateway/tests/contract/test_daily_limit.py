"""The hard daily spend limit, checked before any model call."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from llm_gateway.app import create_app
from llm_gateway.ledger import DaySpend, InMemoryLedger, LedgerError, SpendRecord
from llm_gateway.settings import Settings
from llm_gateway.usage import Usage
from pydantic import SecretStr
from scout_testkit import fake_claude as claude

from ..conftest import AUTH, NOW, Harness, body


class DownLedger(InMemoryLedger):
    async def spent_on(self, day: date) -> DaySpend:
        raise LedgerError("database down")


def spent(cost: str, when: datetime = NOW) -> SpendRecord:
    return SpendRecord(
        created_at=when,
        request_id="r",
        model="claude-sonnet-5-5",
        usage=Usage(),
        cost_usd=Decimal(cost),
        estimated=False,
    )


def test_limit_reached_refuses_without_calling_claude(harness: Callable[..., Harness]) -> None:
    ledger = InMemoryLedger()
    ledger.records.append(spent("15"))
    h = harness(claude.end_turn("no"), ledger=ledger)

    response = h.post()

    assert response.status_code == 402
    assert response.json()["error"]["type"] == "billing_error"
    assert "limit" in response.json()["error"]["message"]
    assert h.fake.requests == []


def test_worst_case_output_counts_before_the_call(harness: Callable[..., Harness]) -> None:
    # 1M Sonnet output tokens could cost $10, which would pass the $5 limit.
    h = harness(claude.end_turn("no"), limit="5")

    response = h.post(max_tokens=1_000_000)

    assert response.status_code == 402
    assert h.fake.requests == []


def test_yesterdays_spend_does_not_count(harness: Callable[..., Harness]) -> None:
    ledger = InMemoryLedger()
    ledger.records.append(spent("15", datetime(2026, 11, 4, 23, 0, tzinfo=UTC)))

    assert harness(claude.end_turn("ok"), ledger=ledger).post().status_code == 200


def test_calls_add_up_until_the_limit_stops_them(harness: Callable[..., Harness]) -> None:
    usage = claude.TokenUsage(input_tokens=500_000, output_tokens=0)
    h = harness(claude.end_turn("a", usage=usage), claude.end_turn("b", usage=usage), limit="1.01")

    assert h.post().status_code == 200  # $1 spent
    assert h.post().status_code == 402
    assert len(h.fake.requests) == 1


def test_unreadable_ledger_fails_closed(harness: Callable[..., Harness]) -> None:
    h = harness(claude.end_turn("no"), ledger=DownLedger())

    response = h.post()

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "api_error"
    assert h.fake.requests == []


def test_readyz_fails_when_the_ledger_is_down(harness: Callable[..., Harness]) -> None:
    response = harness(ledger=DownLedger()).client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["dependencies"] == {"ledger": "unavailable"}


def test_fake_mode_runs_with_no_key_or_database() -> None:
    settings = Settings(
        llm_gateway_mode="fake",
        llm_gateway_token=SecretStr("t"),
        anthropic_api_key=None,
        database_url=None,
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/messages", json=body(), headers={"authorization": "Bearer t"}
        )

    assert response.status_code == 200
    assert response.json()["stop_reason"] == "end_turn"


def test_fake_mode_can_replay_a_script_file(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text(claude.dump_script([claude.tool_use("get_kev", {"cve": "CVE-1"})]))
    settings = Settings(
        llm_gateway_mode="fake",
        llm_gateway_token=SecretStr("t"),
        llm_gateway_fake_script=script,
        database_url=None,
    )

    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/messages", json=body(), headers=AUTH | {"authorization": "Bearer t"})

    assert response.json()["stop_reason"] == "tool_use"
