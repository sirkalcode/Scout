"""Reads token usage from raw API responses, whole or streamed."""

import json
import math
from dataclasses import dataclass, replace
from typing import Any, cast

CHARS_PER_TOKEN_ESTIMATE = 2  # On the high side, so a cut-off stream is never undercharged.


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    web_search_requests: int = 0

    @property
    def total_input_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_input_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
        )


def _as_dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _merge_usage(current: Usage, raw: dict[str, Any]) -> Usage:
    """Applies only the fields present, because a stream reports usage in pieces."""
    changes: dict[str, int] = {}
    for field in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
        if isinstance(raw.get(field), int):
            changes[field] = raw[field]

    # The 5-minute and 1-hour split is the accurate source. The older total means 5-minute writes.
    breakdown = _as_dict(raw.get("cache_creation"))
    if breakdown:
        changes["cache_write_5m_tokens"] = int(breakdown.get("ephemeral_5m_input_tokens") or 0)
        changes["cache_write_1h_tokens"] = int(breakdown.get("ephemeral_1h_input_tokens") or 0)
    elif isinstance(raw.get("cache_creation_input_tokens"), int):
        changes["cache_write_5m_tokens"] = raw["cache_creation_input_tokens"]

    server_tools = _as_dict(raw.get("server_tool_use"))
    if isinstance(server_tools.get("web_search_requests"), int):
        changes["web_search_requests"] = server_tools["web_search_requests"]
    return replace(current, **changes)


def usage_from_message(message: dict[str, Any]) -> Usage:
    return _merge_usage(Usage(), _as_dict(message.get("usage")))


def _delta_chars(event: dict[str, Any]) -> int:
    delta = _as_dict(event.get("delta"))
    return sum(
        len(value)
        for key in ("text", "thinking", "partial_json")
        if isinstance(value := delta.get(key), str)
    )


class StreamUsageTracker:
    """Follows server-sent events in chunks of any size, without changing them."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._data: list[bytes] = []
        self._usage = Usage()
        self._output_chars = 0
        self._final_usage_seen = False
        self.complete = False

    def feed(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        while (end := self._buffer.find(b"\n")) != -1:
            line = bytes(self._buffer[:end]).rstrip(b"\r")
            del self._buffer[: end + 1]
            if not line:
                self._dispatch()
            elif line.startswith(b"data:"):
                self._data.append(line[5:].strip())

    def _dispatch(self) -> None:
        data, self._data = self._data, []
        if not data:
            return
        try:
            event = _as_dict(json.loads(b"\n".join(data)))
        except ValueError:
            return

        match event.get("type"):
            case "message_start":
                raw = _as_dict(_as_dict(event.get("message")).get("usage"))
                self._usage = _merge_usage(self._usage, raw)
            case "content_block_delta":
                self._output_chars += _delta_chars(event)
            case "message_delta":
                self._usage = _merge_usage(self._usage, _as_dict(event.get("usage")))
                self._final_usage_seen = True
            case "message_stop":
                self.complete = True
            case _:
                pass

    @property
    def estimated(self) -> bool:
        """True when the output count was worked out here, not reported by the API."""
        return not self._final_usage_seen

    @property
    def usage(self) -> Usage:
        if self._final_usage_seen:
            return self._usage
        estimate = math.ceil(self._output_chars / CHARS_PER_TOKEN_ESTIMATE)
        return replace(self._usage, output_tokens=max(self._usage.output_tokens, estimate))
