"""A scripted stand-in for the Claude API, used by tests and by llm-gateway's fake mode.

It plugs in as an HTTP transport and speaks the real wire format, so everything above it runs
exactly as it does in production and no test can spend money.
"""

import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx2

DEFAULT_REPLY = "This is a fake reply from FakeClaude."


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read: int = 0
    cache_write: int = 0


@dataclass(frozen=True)
class Step:
    """One scripted answer. A request consumes one step, including each retry."""

    content: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    stop_reason: str = "end_turn"
    usage: TokenUsage = TokenUsage()
    status: int = 200
    error_type: str | None = None
    error_message: str = ""
    retry_after: int | None = None
    connection_error: bool = False
    drop_after_events: int | None = None
    raw_body: str | None = None


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def end_turn(text: str = DEFAULT_REPLY, *, usage: TokenUsage | None = None) -> Step:
    return Step(content=[text_block(text)], usage=usage or TokenUsage())


def tool_use(name: str, tool_input: dict[str, Any], *, usage: TokenUsage | None = None) -> Step:
    block = {"type": "tool_use", "id": "toolu_fake", "name": name, "input": tool_input}
    return Step(content=[block], stop_reason="tool_use", usage=usage or TokenUsage())


def max_tokens(text: str = "cut off") -> Step:
    return Step(content=[text_block(text)], stop_reason="max_tokens")


def pause_turn() -> Step:
    return Step(stop_reason="pause_turn")


def refusal() -> Step:
    return Step(stop_reason="refusal")


def api_error(
    status: int, error_type: str, message: str = "error", *, retry_after: int | None = None
) -> Step:
    return Step(
        status=status, error_type=error_type, error_message=message, retry_after=retry_after
    )


def rate_limited(*, retry_after: int | None = None) -> Step:
    return api_error(429, "rate_limit_error", "Rate limited", retry_after=retry_after)


def overloaded() -> Step:
    return api_error(529, "overloaded_error", "Overloaded")


def connection_error() -> Step:
    return Step(connection_error=True)


def dropped_stream(text: str = "partial", *, after_events: int = 3) -> Step:
    """A stream that loses its connection after this many events."""
    return Step(content=[text_block(text)], drop_after_events=after_events)


def raw_response(body: str) -> Step:
    """A 200 response whose body is exactly this text."""
    return Step(raw_body=body)


def dump_script(steps: Iterable[Step]) -> str:
    return json.dumps([asdict(step) for step in steps])


def load_script(text: str) -> list[Step]:
    steps: list[Step] = []
    for item in json.loads(text):
        fields = dict(item)
        fields["usage"] = TokenUsage(**fields.get("usage", {}))
        steps.append(Step(**fields))
    return steps


def _usage_json(usage: TokenUsage) -> dict[str, Any]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": usage.cache_read,
        "cache_creation_input_tokens": usage.cache_write,
        "cache_creation": {
            "ephemeral_5m_input_tokens": usage.cache_write,
            "ephemeral_1h_input_tokens": 0,
        },
    }


def _message(step: Step, model: str, number: int) -> dict[str, Any]:
    return {
        "id": f"msg_fake_{number}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": step.content,
        "stop_reason": step.stop_reason,
        "stop_sequence": None,
        "usage": _usage_json(step.usage),
    }


def _block_events(index: int, block: dict[str, Any]) -> list[dict[str, Any]]:
    if block["type"] == "text":
        opening: dict[str, Any] = {"type": "text", "text": ""}
        delta: dict[str, Any] = {"type": "text_delta", "text": block["text"]}
    else:
        opening = {**block, "input": {}}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
    return [
        {"type": "content_block_start", "index": index, "content_block": opening},
        {"type": "content_block_delta", "index": index, "delta": delta},
        {"type": "content_block_stop", "index": index},
    ]


def _stream_events(step: Step, model: str, number: int) -> list[dict[str, Any]]:
    started: dict[str, Any] = {
        **_message(step, model, number),
        "content": [],
        "stop_reason": None,
    }
    started["usage"] = {**_usage_json(step.usage), "output_tokens": 1}
    events: list[dict[str, Any]] = [{"type": "message_start", "message": started}]
    for index, block in enumerate(step.content):
        events.extend(_block_events(index, block))
    events.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": step.stop_reason, "stop_sequence": None},
            # Like the real API, the final event repeats the full usage, input included.
            "usage": _usage_json(step.usage),
        }
    )
    events.append({"type": "message_stop"})
    return events


class _EventStream(httpx2.AsyncByteStream):
    def __init__(self, events: list[dict[str, Any]], drop_after: int | None) -> None:
        self._events = events
        self._drop_after = drop_after

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for position, event in enumerate(self._events):
            if position == self._drop_after:
                raise httpx2.ReadError("FakeClaude dropped the stream")
            yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


@dataclass(frozen=True)
class RecordedRequest:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


class FakeClaude(httpx2.AsyncBaseTransport):
    """Replays `steps` in order. With no script, it gives the default reply forever."""

    def __init__(self, steps: list[Step] | None = None) -> None:
        self._steps = steps
        self.requests: list[RecordedRequest] = []

    def _next_step(self) -> Step:
        if self._steps is None:
            return end_turn()
        assert self._steps, "FakeClaude's script ran out: the code made an unexpected call"  # noqa: S101
        return self._steps.pop(0)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body: dict[str, Any] = json.loads(await request.aread())
        self.requests.append(RecordedRequest(request.url.path, dict(request.headers), body))
        step = self._next_step()
        number = len(self.requests)
        request_id = {"request-id": f"req_fake_{number}"}

        if step.connection_error:
            raise httpx2.ConnectError("FakeClaude refused the connection")
        if step.error_type is not None:
            headers = dict(request_id)
            if step.retry_after is not None:
                headers["retry-after"] = str(step.retry_after)
            error = {"type": step.error_type, "message": step.error_message}
            return httpx2.Response(
                step.status, json={"type": "error", "error": error}, headers=headers
            )
        if step.raw_body is not None:
            return httpx2.Response(200, text=step.raw_body, headers=request_id)
        if body.get("stream"):
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream", **request_id},
                stream=_EventStream(
                    _stream_events(step, str(body.get("model")), number), step.drop_after_events
                ),
            )
        return httpx2.Response(
            200, json=_message(step, str(body.get("model")), number), headers=request_id
        )
