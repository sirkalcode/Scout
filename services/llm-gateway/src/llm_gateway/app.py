"""llm-gateway: the one service that holds the Claude key and controls what is spent.

The API matches Anthropic's Messages endpoint, so ChatAnthropic works unchanged. Requests go
through untouched. Costs are worked out here from the raw usage in each response.
"""

import asyncio
import hmac
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from typing import Any, cast

import anyio
import httpx2
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

from scout_contracts.health import DependencyStatus, HealthResponse, ReadinessResponse
from scout_contracts.llm import SpendToday
from scout_testkit.fake_claude import FakeClaude, load_script

from .budget import BudgetExceeded, BudgetGuard, Reservation
from .ledger import InMemoryLedger, LedgerError, PostgresLedger, SpendLedger, SpendRecord
from .migrate import upgrade_to_head
from .pricing import cost_usd, is_priced, worst_case_cost_usd
from .settings import Settings
from .upstream import Upstream, UpstreamResult, UpstreamUnavailable, relay_stream
from .usage import StreamUsageTracker, Usage, usage_from_message

logger = logging.getLogger(__name__)

SERVICE = "llm-gateway"
SAMPLING_PARAMETERS = ("temperature", "top_p", "top_k")
# These change the price, and the cost math does not cover them yet.
UNPRICED_OPTIONS = ("speed", "inference_geo")
PASSED_BACK_HEADERS = ("content-type", "retry-after", "request-id")
PASSED_BACK_PREFIX = "anthropic-ratelimit-"
UPSTREAM_TIMEOUT = httpx2.Timeout(connect=10, read=600, write=60, pool=10)


def api_error(status: int, error_type: str, message: str) -> JSONResponse:
    """An error in Anthropic's format, so SDKs handle it like any other API error."""
    return JSONResponse(
        {"type": "error", "error": {"type": error_type, "message": message}}, status_code=status
    )


def _invalid(message: str) -> JSONResponse:
    return api_error(400, "invalid_request_error", message)


def _validate(payload: dict[str, Any]) -> JSONResponse | None:
    for name in SAMPLING_PARAMETERS:
        if name in payload:
            return _invalid(f"{name} is not allowed. Current Claude models reject it.")
    for name in UNPRICED_OPTIONS:
        if name in payload:
            return _invalid(f"{name} changes the price and is not supported by llm-gateway.")
    model = payload.get("model")
    if not isinstance(model, str) or not is_priced(model):
        return _invalid(f"model has no known price: {model!r}")
    max_tokens = payload.get("max_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        return _invalid("max_tokens must be a positive whole number")
    return None


def _passed_back_headers(response: httpx2.Response) -> dict[str, str]:
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() in PASSED_BACK_HEADERS or key.lower().startswith(PASSED_BACK_PREFIX)
    }


class _RelayResponse(StreamingResponse):
    """Closes the relay when the caller leaves, so cost is settled straight away."""

    def __init__(self, relay: AsyncIterator[bytes], headers: dict[str, str]) -> None:
        super().__init__(relay, status_code=200, headers=headers)
        self._relay = relay

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await cast(Any, self._relay).aclose()


def _build_ledger(settings: Settings) -> SpendLedger:
    if settings.database_url is None:
        return InMemoryLedger()
    return PostgresLedger(settings.database_url.get_secret_value())


def _build_fake(settings: Settings) -> FakeClaude:
    if settings.llm_gateway_fake_script is None:
        return FakeClaude()
    return FakeClaude(load_script(settings.llm_gateway_fake_script.read_text()))


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    ledger: SpendLedger | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    config = settings or Settings()
    store = ledger or _build_ledger(config)
    now = clock or (lambda: datetime.now(UTC))
    budget = BudgetGuard(store, config.llm_daily_limit_usd, clock=now)
    if transport is None and config.llm_gateway_mode == "fake":
        transport = _build_fake(config)
    client = httpx2.AsyncClient(
        base_url=config.anthropic_base_url,
        transport=transport,
        timeout=UPSTREAM_TIMEOUT,
        trust_env=False,
    )
    key = config.anthropic_api_key.get_secret_value() if config.anthropic_api_key else None
    upstream = Upstream(client, api_key=key, default_version=config.anthropic_version, sleep=sleep)
    token = config.llm_gateway_token.get_secret_value().encode()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        if isinstance(store, PostgresLedger) and config.database_url is not None:
            url = config.database_url.get_secret_value()
            await anyio.to_thread.run_sync(upgrade_to_head, url)
        yield
        await client.aclose()
        await store.close()

    app = FastAPI(title=SERVICE, version=version(SERVICE), lifespan=lifespan)

    def authorized(request: Request) -> bool:
        supplied = request.headers.get("authorization", "").encode()
        return hmac.compare_digest(supplied, b"Bearer " + token)

    def spend_record(request_id: str, model: str, usage: Usage, estimated: bool) -> SpendRecord:
        return SpendRecord(
            created_at=now(),
            request_id=request_id,
            model=model,
            usage=usage,
            cost_usd=cost_usd(model, usage),
            estimated=estimated,
        )

    async def finish_message(
        result: UpstreamResult, model: str, reservation: Reservation
    ) -> Response:
        response = result.response
        headers = _passed_back_headers(response)
        if response.status_code != 200:
            await budget.settle(reservation, None)
            return Response(response.content, response.status_code, headers)

        request_id = response.headers.get("request-id", "unknown")
        try:
            record = spend_record(request_id, model, usage_from_message(response.json()), False)
        except ValueError:
            # The usage is unreadable but the call may be billed, so count the amount reserved.
            record = SpendRecord(
                created_at=now(),
                request_id=request_id,
                model=model,
                usage=Usage(),
                cost_usd=reservation.amount,
                estimated=True,
            )
        await budget.settle(reservation, record)
        headers["x-scout-cost-usd"] = str(record.cost_usd)
        return Response(response.content, 200, headers)

    def relay_message(result: UpstreamResult, model: str, reservation: Reservation) -> Response:
        response = result.response
        request_id = response.headers.get("request-id", "unknown")
        chunks = result.chunks
        assert chunks is not None  # noqa: S101

        async def source() -> AsyncIterator[bytes]:
            yield result.first_chunk
            async for chunk in chunks:
                yield chunk

        async def on_done(tracker: StreamUsageTracker) -> None:
            try:
                await response.aclose()
            finally:
                billed = tracker.usage != Usage()
                record = (
                    spend_record(request_id, model, tracker.usage, tracker.estimated)
                    if billed
                    else None
                )
                await budget.settle(reservation, record)

        relay = relay_stream(source(), on_done)
        return _RelayResponse(relay, _passed_back_headers(response))

    @app.post("/v1/messages")
    async def messages(request: Request) -> Response:
        if not authorized(request):
            return api_error(401, "authentication_error", "Invalid bearer token")

        raw = await request.body()
        try:
            parsed: object = json.loads(raw)
        except ValueError:
            return _invalid("The request body is not valid JSON")
        if not isinstance(parsed, dict):
            return _invalid("The request body must be a JSON object")
        payload = cast(dict[str, Any], parsed)
        if (problem := _validate(payload)) is not None:
            return problem

        model: str = payload["model"]
        worst_case = worst_case_cost_usd(
            model, max_tokens=payload["max_tokens"], body_bytes=len(raw)
        )
        try:
            reservation = await budget.reserve(worst_case)
        except BudgetExceeded as error:
            return api_error(402, "billing_error", str(error))
        except LedgerError:
            logger.exception("Refusing the call because spend could not be read")
            return api_error(503, "api_error", "The spend ledger is unavailable")

        handed_over = False
        try:
            result = await upstream.send(
                raw,
                stream=payload.get("stream") is True,
                version=request.headers.get("anthropic-version"),
                beta=request.headers.get("anthropic-beta"),
            )
            handed_over = True
        except UpstreamUnavailable:
            logger.warning("Could not reach Claude after every attempt")
            return api_error(502, "api_error", "Could not reach Claude")
        finally:
            if not handed_over:
                with anyio.CancelScope(shield=True):
                    await budget.settle(reservation, None)

        if result.chunks is not None:
            return relay_message(result, model, reservation)
        return await finish_message(result, model, reservation)

    @app.get("/v1/spend/today", response_model=SpendToday)
    async def spend_today(request: Request) -> Response:
        if not authorized(request):
            return api_error(401, "authentication_error", "Invalid bearer token")
        try:
            summary = await budget.summary()
        except LedgerError:
            return api_error(503, "api_error", "The spend ledger is unavailable")
        report = SpendToday(
            date=summary.day,
            mode=config.llm_gateway_mode,
            limit_usd=summary.limit_usd,
            spent_usd=summary.spent_usd,
            remaining_usd=summary.remaining_usd,
            requests=summary.requests,
        )
        return Response(report.model_dump_json(), media_type="application/json")

    @app.get("/healthz")
    async def healthz() -> HealthResponse:
        return HealthResponse(service=SERVICE, version=app.version)

    @app.get("/readyz", response_model=ReadinessResponse)
    async def readyz() -> JSONResponse:
        status: DependencyStatus = "ok"
        try:
            await store.spent_on(now().date())
        except LedgerError:
            status = "unavailable"
        report = ReadinessResponse(
            status="ready" if status == "ok" else "not_ready",
            service=SERVICE,
            dependencies={"ledger": status},
        )
        return JSONResponse(report.model_dump(), status_code=200 if status == "ok" else 503)

    return app


def main_app() -> FastAPI:
    return create_app()
