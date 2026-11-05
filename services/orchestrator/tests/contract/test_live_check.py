"""The live check reports text, tokens and cost for one normal and one streaming call."""

import pytest

from orchestrator.live_check import run
from orchestrator.settings import Settings

from .conftest import FakeAnthropicApi


@pytest.mark.enable_socket
def test_report_covers_both_calls_and_the_ledger(fake_api: FakeAnthropicApi) -> None:
    report = run(Settings(llm_gateway_url=fake_api.url))

    assert report["mode"] == "fake"
    assert report["invoke"]["text"] == "Checking KEV."
    assert report["invoke"]["raw_usage"]["input_tokens"] == 10
    assert report["stream"]["text"] == "Patch first."
    assert report["ledger"]["requests_added"] == 2
    assert float(report["ledger"]["cost_added_usd"]) > 0
    assert [request.body.get("model") for request in fake_api.requests] == ["claude-haiku-5-5"] * 2
