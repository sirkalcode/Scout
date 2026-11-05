"""The main agent engine. Agents arrive on Day 4, once the model gateway exists."""

from importlib.metadata import version

from fastapi import FastAPI
from scout_contracts.health import HealthResponse

SERVICE = "orchestrator"


def create_app() -> FastAPI:
    app = FastAPI(title="Scout orchestrator", version=version(SERVICE))

    @app.get("/healthz")
    async def healthz() -> HealthResponse:
        return HealthResponse(service=SERVICE, version=app.version)

    return app


app = create_app()
