"""The public entry point. Later days add auth, rate limits and the run API."""

from importlib.metadata import version

import httpx2
from fastapi import FastAPI, Response, status
from pydantic import ValidationError

from gateway.settings import Settings
from scout_contracts.health import HealthResponse, ReadinessResponse

SERVICE = "gateway"
DEPENDENCY = "orchestrator"


def create_app(
    settings: Settings | None = None,
    orchestrator_transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    config = settings or Settings()
    app = FastAPI(title="Scout gateway", version=version(SERVICE))

    async def orchestrator_is_healthy() -> bool:
        try:
            async with httpx2.AsyncClient(
                base_url=config.orchestrator_url,
                timeout=config.dependency_timeout_seconds,
                transport=orchestrator_transport,
                trust_env=False,
            ) as client:
                response = await client.get("/healthz")
            response.raise_for_status()
            health = HealthResponse.model_validate_json(response.content)
        except (httpx2.HTTPError, ValidationError):
            return False
        return health.service == DEPENDENCY

    @app.get("/healthz")
    async def healthz() -> HealthResponse:
        return HealthResponse(service=SERVICE, version=app.version)

    @app.get(
        "/readyz",
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
    )
    async def readyz(response: Response) -> ReadinessResponse:
        if await orchestrator_is_healthy():
            return ReadinessResponse(
                status="ready", service=SERVICE, dependencies={DEPENDENCY: "ok"}
            )
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="not_ready", service=SERVICE, dependencies={DEPENDENCY: "unavailable"}
        )

    return app


app = create_app()
