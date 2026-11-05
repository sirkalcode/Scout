"""Every service answers /healthz with the shared HealthResponse contract."""

from fastapi.testclient import TestClient

from gateway.app import create_app
from scout_contracts.health import HealthResponse


def test_healthz_returns_the_shared_contract() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    health = HealthResponse.model_validate(response.json())
    assert health.status == "ok"
    assert health.service == "gateway"


def test_openapi_documents_healthz_with_the_shared_schema() -> None:
    spec = create_app().openapi()

    ok = spec["paths"]["/healthz"]["get"]["responses"]["200"]
    schema = ok["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/HealthResponse")


def test_openapi_documents_readyz_success_and_failure() -> None:
    responses = create_app().openapi()["paths"]["/readyz"]["get"]["responses"]

    for status in ("200", "503"):
        schema = responses[status]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/ReadinessResponse")
