"""Streams pass through untouched, retry only before the first byte, and are always costed."""

from collections.abc import AsyncIterator, Callable
from decimal import Decimal

import pytest

from llm_gateway.upstream import relay_stream
from llm_gateway.usage import StreamUsageTracker
from scout_testkit import fake_claude as claude

from ..conftest import Harness


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_events_pass_through_in_order(harness: Callable[..., Harness]) -> None:
    response = harness(claude.end_turn("Patch first.")).post(stream=True)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    types = [
        line.removeprefix("event: ") for line in response.text.splitlines() if "event: " in line
    ]
    assert types == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert "Patch first." in response.text


def test_stream_cost_comes_from_raw_usage(harness: Callable[..., Harness]) -> None:
    usage = claude.TokenUsage(
        input_tokens=1000, output_tokens=100, cache_read=2000, cache_write=400
    )
    h = harness(claude.end_turn("hi", usage=usage))

    h.post(stream=True)

    assert h.ledger.records[0].cost_usd == Decimal("0.0042")
    assert not h.ledger.records[0].estimated


def test_failure_before_the_first_byte_is_retried(harness: Callable[..., Harness]) -> None:
    h = harness(claude.overloaded(), claude.connection_error(), claude.end_turn("ok"))

    response = h.post(stream=True)

    assert response.status_code == 200
    assert "message_stop" in response.text
    assert len(h.fake.requests) == 3


def test_connection_lost_before_the_first_event_is_retried(
    harness: Callable[..., Harness],
) -> None:
    h = harness(claude.dropped_stream("x", after_events=0), claude.end_turn("ok"))

    response = h.post(stream=True)

    assert "message_stop" in response.text
    assert "event: error" not in response.text
    assert len(h.fake.requests) == 2


def test_stream_that_never_starts_returns_the_error_as_json(
    harness: Callable[..., Harness],
) -> None:
    h = harness(claude.overloaded(), claude.overloaded(), claude.overloaded())

    response = h.post(stream=True)

    assert response.status_code == 529
    assert response.json()["error"]["type"] == "overloaded_error"


def test_dropped_stream_ends_with_an_error_event_and_is_not_retried(
    harness: Callable[..., Harness],
) -> None:
    h = harness(claude.dropped_stream("Partial answer", after_events=3), claude.end_turn("no"))

    response = h.post(stream=True)

    assert response.status_code == 200
    assert "Partial answer" in response.text
    assert response.text.rstrip().splitlines()[-2] == "event: error"
    assert len(h.fake.requests) == 1


def test_dropped_stream_still_charges_what_was_used(harness: Callable[..., Harness]) -> None:
    h = harness(claude.dropped_stream("x" * 40, after_events=3))

    h.post(stream=True)

    record = h.ledger.records[0]
    assert record.estimated
    assert record.usage.input_tokens > 0
    assert record.usage.output_tokens == 20
    assert record.cost_usd > 0


def test_dropped_stream_releases_its_reservation(harness: Callable[..., Harness]) -> None:
    h = harness(claude.dropped_stream("x", after_events=1), claude.end_turn("ok"), limit="0.002")

    h.post(stream=True)

    assert h.post(stream=True).status_code == 200


@pytest.mark.anyio
async def test_caller_disconnect_still_settles_the_cost() -> None:
    settled: list[StreamUsageTracker] = []

    async def source() -> AsyncIterator[bytes]:
        yield b'event: message_start\ndata: {"type":"message_start","message":{"usage":'
        yield b'{"input_tokens":10,"output_tokens":1}}}\n\n'
        yield b"never reached"

    async def on_done(tracker: StreamUsageTracker) -> None:
        settled.append(tracker)

    relay = relay_stream(source(), on_done)
    await anext(relay)
    await anext(relay)
    await relay.aclose()

    assert len(settled) == 1
    assert settled[0].usage.input_tokens == 10
