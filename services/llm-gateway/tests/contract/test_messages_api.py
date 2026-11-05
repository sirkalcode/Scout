"""The Anthropic-compatible /v1/messages endpoint: auth, validation and pass-through."""

from collections.abc import Callable
from decimal import Decimal

import pytest

from llm_gateway.ledger import InMemoryLedger
from scout_testkit import fake_claude as claude

from ..conftest import AUTH, NOW, Harness, body


def error_type(response) -> str:  # type: ignore[no-untyped-def]
    payload = response.json()
    assert payload["type"] == "error"
    return payload["error"]["type"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": "Bearer wrong"},
        {"authorization": "internal-token"},
        {"x-api-key": "internal-token"},
    ],
    ids=["none", "wrong-token", "no-bearer-prefix", "x-api-key"],
)
def test_requests_need_the_bearer_token(
    harness: Callable[..., Harness], headers: dict[str, str]
) -> None:
    h = harness()

    response = h.client.post("/v1/messages", json=body(), headers=headers)

    assert response.status_code == 401
    assert error_type(response) == "authentication_error"
    assert h.fake.requests == []


@pytest.mark.parametrize(
    "parameter", [{"temperature": 0.0}, {"temperature": 1.0}, {"top_p": 0.5}, {"top_k": 3}]
)
def test_sampling_parameters_are_rejected_before_any_call(
    harness: Callable[..., Harness], parameter: dict[str, float]
) -> None:
    h = harness()

    response = h.post(**parameter)

    assert response.status_code == 400
    assert error_type(response) == "invalid_request_error"
    assert next(iter(parameter)) in response.json()["error"]["message"]
    assert h.fake.requests == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "claude-made-up"},
        {"max_tokens": 0},
        {"max_tokens": "many"},
        {"speed": "fast"},
        {"inference_geo": "us"},
    ],
    ids=["unknown-model", "zero-tokens", "bad-tokens", "fast-mode", "data-residency"],
)
def test_requests_that_cannot_be_priced_are_rejected(
    harness: Callable[..., Harness], overrides: dict[str, object]
) -> None:
    h = harness()

    response = h.post(**overrides)

    assert response.status_code == 400
    assert error_type(response) == "invalid_request_error"
    assert h.fake.requests == []


def test_missing_max_tokens_is_rejected(harness: Callable[..., Harness]) -> None:
    h = harness()
    payload = body()
    del payload["max_tokens"]

    assert h.client.post("/v1/messages", json=payload, headers=AUTH).status_code == 400


def test_invalid_json_is_rejected(harness: Callable[..., Harness]) -> None:
    response = harness().client.post("/v1/messages", content=b"{nope", headers=AUTH)

    assert response.status_code == 400
    assert error_type(response) == "invalid_request_error"


def test_request_body_is_forwarded_unchanged(harness: Callable[..., Harness]) -> None:
    h = harness(claude.end_turn("hi"))
    extras = {
        "system": [{"type": "text", "text": "S", "cache_control": {"type": "ephemeral"}}],
        "tools": [{"name": "t", "input_schema": {"type": "object"}}],
    }

    h.post(**extras)

    assert h.fake.requests[0].body == body(**extras)
    assert h.fake.requests[0].path == "/v1/messages"


def test_real_key_replaces_the_internal_token(harness: Callable[..., Harness]) -> None:
    h = harness(claude.end_turn("hi"))

    h.post()

    headers = h.fake.requests[0].headers
    assert headers["x-api-key"] == "real-claude-key"
    assert "authorization" not in headers


def test_the_real_key_never_reaches_the_caller(harness: Callable[..., Harness]) -> None:
    response = harness(claude.end_turn("hi")).post()

    assert "real-claude-key" not in response.text
    assert "real-claude-key" not in str(response.headers)


def test_version_and_beta_headers_are_forwarded(harness: Callable[..., Harness]) -> None:
    h = harness(claude.end_turn("hi"))
    headers = {**AUTH, "anthropic-version": "2026-10-21", "anthropic-beta": "some-beta"}

    h.client.post("/v1/messages", json=body(), headers=headers)

    assert h.fake.requests[0].headers["anthropic-version"] == "2026-10-21"
    assert h.fake.requests[0].headers["anthropic-beta"] == "some-beta"


def test_default_version_is_set_when_the_caller_sends_none(
    harness: Callable[..., Harness],
) -> None:
    h = harness(claude.end_turn("hi"))

    h.post()

    assert h.fake.requests[0].headers["anthropic-version"] == "2026-10-21"


@pytest.mark.parametrize(
    ("step", "stop_reason"),
    [
        (claude.end_turn("done"), "end_turn"),
        (claude.tool_use("get_kev", {"cve": "CVE-2021-44228"}), "tool_use"),
        (claude.max_tokens("cut"), "max_tokens"),
        (claude.pause_turn(), "pause_turn"),
        (claude.refusal(), "refusal"),
    ],
    ids=["end_turn", "tool_use", "max_tokens", "pause_turn", "refusal"],
)
def test_every_stop_reason_passes_through(
    harness: Callable[..., Harness], step: claude.Step, stop_reason: str
) -> None:
    response = harness(step).post()

    assert response.status_code == 200
    assert response.json()["stop_reason"] == stop_reason


def test_cost_comes_from_raw_usage(harness: Callable[..., Harness]) -> None:
    # Sonnet: 1M input $2, 1M output $10, 1M cache read $0.10, 1M 5-minute write $2.50.
    usage = claude.TokenUsage(
        input_tokens=1000, output_tokens=100, cache_read=2000, cache_write=400
    )
    h = harness(claude.end_turn("hi", usage=usage))

    response = h.post()

    assert Decimal(response.headers["x-scout-cost-usd"]) == Decimal("0.0042")
    assert h.ledger.records[0].cost_usd == Decimal("0.0042")
    assert h.ledger.records[0].usage.input_tokens == 1000
    assert h.ledger.records[0].created_at == NOW


def test_unreadable_success_is_charged_at_the_reserved_amount(
    harness: Callable[..., Harness],
) -> None:
    h = harness(claude.raw_response("not json"))

    response = h.post()

    assert response.status_code == 200
    assert response.text == "not json"
    assert h.ledger.records[0].estimated
    assert h.ledger.records[0].cost_usd > 0


def test_upstream_errors_pass_through_with_no_charge(harness: Callable[..., Harness]) -> None:
    h = harness(claude.api_error(400, "invalid_request_error", "Bad thing"))

    response = h.post()

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Bad thing"
    assert h.ledger.records == []


def test_healthz_returns_the_shared_contract(harness: Callable[..., Harness]) -> None:
    response = harness().client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["service"] == "llm-gateway"


def test_readyz_reports_a_working_ledger(harness: Callable[..., Harness]) -> None:
    response = harness().client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {"ledger": "ok"}


def test_spend_needs_the_token_and_reports_today(harness: Callable[..., Harness]) -> None:
    h = harness(claude.end_turn("hi"), limit="2")
    h.post()

    assert h.client.get("/v1/spend/today").status_code == 401
    summary = h.client.get("/v1/spend/today", headers=AUTH).json()

    assert summary["date"] == "2026-11-05"
    assert summary["requests"] == 1
    assert Decimal(summary["limit_usd"]) == Decimal(2)
    assert Decimal(summary["spent_usd"]) > 0


def test_ledger_can_be_shared_between_app_instances(harness: Callable[..., Harness]) -> None:
    ledger = InMemoryLedger()
    harness(claude.end_turn("hi"), ledger=ledger).post()

    second = harness(ledger=ledger)

    assert second.client.get("/v1/spend/today", headers=AUTH).json()["requests"] == 1
