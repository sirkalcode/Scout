"""Readiness depends on the orchestrator, while liveness never does."""

import httpx2
import pytest
from fastapi.testclient import TestClient
from pytest_socket import SocketBlockedError

from gateway.app import create_app
from gateway.settings import Settings
from scout_contracts.health import ReadinessResponse

HEALTHY = {"status": "ok", "service": "orchestrator", "version": "0.1.0"}


def client_with(handler: httpx2.MockTransport) -> TestClient:
    settings = Settings(orchestrator_url="http://orchestrator.test")
    return TestClient(create_app(settings, orchestrator_transport=handler))


def test_ready_when_the_orchestrator_is_healthy() -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json=HEALTHY)

    response = client_with(httpx2.MockTransport(handler)).get("/readyz")

    assert response.status_code == 200
    ready = ReadinessResponse.model_validate(response.json())
    assert ready.status == "ready"
    assert ready.dependencies == {"orchestrator": "ok"}
    assert seen == ["http://orchestrator.test/healthz"]


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(500),
        httpx2.Response(200, json={"status": "broken"}),
        httpx2.Response(200, text="not json"),
        httpx2.Response(200, json={**HEALTHY, "service": "something-else"}),
    ],
    ids=["server-error", "wrong-status", "not-json", "wrong-service"],
)
def test_not_ready_when_the_orchestrator_answers_badly(response: httpx2.Response) -> None:
    result = client_with(httpx2.MockTransport(lambda _: response)).get("/readyz")

    assert result.status_code == 503
    ready = ReadinessResponse.model_validate(result.json())
    assert ready.status == "not_ready"
    assert ready.dependencies == {"orchestrator": "unavailable"}


def test_not_ready_when_the_orchestrator_cannot_be_reached() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    result = client_with(httpx2.MockTransport(handler)).get("/readyz")

    assert result.status_code == 503


def test_liveness_ignores_the_orchestrator() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    assert client_with(httpx2.MockTransport(handler)).get("/healthz").status_code == 200


def test_fast_tests_cannot_open_network_sockets() -> None:
    with pytest.raises(SocketBlockedError):
        httpx2.get("http://127.0.0.1:9/")
