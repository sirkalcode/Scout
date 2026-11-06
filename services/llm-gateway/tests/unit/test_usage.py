"""Usage is read from the raw API fields, for whole responses and for streams in any chunking."""

import json
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from llm_gateway.usage import StreamUsageTracker, Usage, usage_from_message


def sse(*events: dict[str, Any]) -> bytes:
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )


START = {
    "type": "message_start",
    "message": {
        "usage": {
            "input_tokens": 10,
            "output_tokens": 1,
            "cache_read_input_tokens": 7,
            "cache_creation_input_tokens": 3,
        }
    },
}
DELTA = {
    "type": "content_block_delta",
    "index": 0,
    "delta": {"type": "text_delta", "text": "x" * 40},
}
END = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 9}}
STOP = {"type": "message_stop"}
EXPECTED = Usage(
    input_tokens=10, output_tokens=9, cache_read_input_tokens=7, cache_write_5m_tokens=3
)


def test_message_usage_keeps_input_tokens_separate_from_cache() -> None:
    usage = usage_from_message(
        {"usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 7}}
    )

    assert usage == Usage(input_tokens=10, output_tokens=5, cache_read_input_tokens=7)


def test_cache_write_breakdown_is_used_when_present() -> None:
    usage = usage_from_message(
        {
            "usage": {
                "cache_creation_input_tokens": 30,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 10,
                    "ephemeral_1h_input_tokens": 20,
                },
            }
        }
    )

    assert (usage.cache_write_5m_tokens, usage.cache_write_1h_tokens) == (10, 20)


def test_web_search_count_is_read() -> None:
    usage = usage_from_message({"usage": {"server_tool_use": {"web_search_requests": 2}}})

    assert usage.web_search_requests == 2


def test_missing_usage_is_empty() -> None:
    assert usage_from_message({}) == Usage()


def test_complete_stream_uses_the_reported_totals() -> None:
    tracker = StreamUsageTracker()

    tracker.feed(sse(START, DELTA, END, STOP))

    assert tracker.usage == EXPECTED
    assert tracker.complete
    assert not tracker.estimated


def test_later_events_can_update_input_fields() -> None:
    tracker = StreamUsageTracker()
    late = {
        "type": "message_delta",
        "delta": {},
        "usage": {"input_tokens": 11, "output_tokens": 9, "cache_read_input_tokens": 8},
    }

    tracker.feed(sse(START, late, STOP))

    assert tracker.usage.input_tokens == 11
    assert tracker.usage.cache_read_input_tokens == 8


def test_cut_off_stream_estimates_output_from_the_text_seen() -> None:
    tracker = StreamUsageTracker()

    tracker.feed(sse(START, DELTA))

    assert not tracker.complete
    assert tracker.estimated
    assert tracker.usage.input_tokens == 10
    assert tracker.usage.output_tokens == 20  # 40 characters at 2 per token, on the high side


def test_stream_cut_before_any_usage_is_empty() -> None:
    tracker = StreamUsageTracker()

    tracker.feed(b'event: message_start\ndata: {"type":')

    assert tracker.usage == Usage()
    assert not tracker.complete


def test_junk_and_crlf_lines_do_not_break_tracking() -> None:
    tracker = StreamUsageTracker()
    junk = b": keep-alive\n\ndata: not json\n\n"

    tracker.feed(junk + sse(START, END, STOP).replace(b"\n", b"\r\n"))

    assert tracker.usage == Usage(
        input_tokens=10, output_tokens=9, cache_read_input_tokens=7, cache_write_5m_tokens=3
    )


@given(st.lists(st.integers(1, 40), min_size=1, max_size=200))
def test_any_chunking_gives_the_same_result(sizes: list[int]) -> None:
    data = sse(START, DELTA, END, STOP)
    tracker = StreamUsageTracker()
    position = 0
    index = 0

    while position < len(data):
        size = sizes[index % len(sizes)]
        tracker.feed(data[position : position + size])
        position += size
        index += 1

    assert tracker.usage == EXPECTED
    assert tracker.complete
