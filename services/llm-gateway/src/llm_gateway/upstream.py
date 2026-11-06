"""Sends requests to Claude with retries, and relays streams while counting their usage."""

import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import anyio
import httpx2

from .usage import StreamUsageTracker

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 529})
MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 0.5
MAX_RETRY_AFTER_SECONDS = 60.0

STREAM_INTERRUPTED = (
    b"\n\nevent: error\n"
    b'data: {"type":"error","error":{"type":"api_error",'
    b'"message":"The stream from Claude was interrupted"}}\n\n'
)


class UpstreamUnavailable(Exception):
    """Claude could not be reached after every attempt."""


@dataclass
class UpstreamResult:
    """A response. For a live stream, `first_chunk` and `chunks` hold the body."""

    response: httpx2.Response
    first_chunk: bytes = b""
    chunks: AsyncIterator[bytes] | None = None


def _backoff(attempt: int) -> float:
    return BASE_DELAY_SECONDS * 2**attempt * random.uniform(0.75, 1.25)  # noqa: S311


def _retry_after(response: httpx2.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


class Upstream:
    def __init__(
        self,
        client: httpx2.AsyncClient,
        *,
        api_key: str | None,
        default_version: str,
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._default_version = default_version
        self._sleep = sleep

    def _headers(self, version: str | None, beta: str | None) -> dict[str, str]:
        # An allowlist: the caller's internal token must never reach Anthropic.
        headers = {
            "content-type": "application/json",
            "accept-encoding": "identity",
            "anthropic-version": version or self._default_version,
        }
        if beta:
            headers["anthropic-beta"] = beta
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    async def _attempt(self, request: httpx2.Request, stream: bool) -> UpstreamResult:
        response = await self._client.send(request, stream=True)
        if not stream or response.status_code != 200:
            try:
                await response.aread()
            finally:
                await response.aclose()
            return UpstreamResult(response)

        # Nothing has reached the caller until the first chunk, so a failure here is retryable.
        chunks = response.aiter_raw()
        try:
            first_chunk = await anext(chunks, b"")
        except BaseException:
            await response.aclose()
            raise
        return UpstreamResult(response, first_chunk, chunks)

    async def send(
        self, body: bytes, *, stream: bool, version: str | None, beta: str | None
    ) -> UpstreamResult:
        headers = self._headers(version, beta)
        for attempt in range(MAX_ATTEMPTS):
            is_last = attempt == MAX_ATTEMPTS - 1
            request = self._client.build_request(
                "POST", "/v1/messages", content=body, headers=headers
            )
            try:
                result = await self._attempt(request, stream)
            except httpx2.TransportError as error:
                if is_last:
                    raise UpstreamUnavailable(type(error).__name__) from error
                await self._sleep(_backoff(attempt))
                continue

            if result.response.status_code not in RETRYABLE_STATUS or is_last:
                return result
            wait = _retry_after(result.response)
            if wait is not None and wait > MAX_RETRY_AFTER_SECONDS:
                return result  # Too long to hold a request open. The caller can decide.
            await self._sleep(wait if wait is not None else _backoff(attempt))
        raise AssertionError("The retry loop always returns or raises")  # pragma: no cover


async def relay_stream(
    source: AsyncIterator[bytes],
    on_done: Callable[[StreamUsageTracker], Awaitable[None]],
) -> AsyncIterator[bytes]:
    """Yields the bytes unchanged and always reports usage, even if the caller disconnects."""
    tracker = StreamUsageTracker()
    try:
        try:
            async for chunk in source:
                tracker.feed(chunk)
                yield chunk
        except httpx2.TransportError:
            yield STREAM_INTERRUPTED
    finally:
        with anyio.CancelScope(shield=True):
            await on_done(tracker)
