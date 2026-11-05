"""Every service answers /healthz with the shared HealthResponse contract."""

import socket

import pytest
from fastapi.testclient import TestClient
from orchestrator.app import create_app
from pytest_socket import SocketBlockedError
from scout_contracts.health import HealthResponse


def test_healthz_returns_the_shared_contract() -> None:
    response = TestClient(create_app()).get("/healthz")

    assert response.status_code == 200
    health = HealthResponse.model_validate(response.json())
    assert health.status == "ok"
    assert health.service == "orchestrator"


def test_openapi_documents_healthz_with_the_shared_schema() -> None:
    ok = create_app().openapi()["paths"]["/healthz"]["get"]["responses"]["200"]

    assert ok["content"]["application/json"]["schema"]["$ref"].endswith("/HealthResponse")


def test_fast_tests_cannot_open_network_sockets() -> None:
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
