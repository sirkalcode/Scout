"""Health and readiness responses shared by every Scout service."""

from typing import Literal

from pydantic import BaseModel

DependencyStatus = Literal["ok", "unavailable"]


class HealthResponse(BaseModel):
    """Liveness: the process is up. It never depends on another service."""

    status: Literal["ok"] = "ok"
    service: str
    version: str


class ReadinessResponse(BaseModel):
    """Readiness: the service can do useful work, because its dependencies answer."""

    status: Literal["ready", "not_ready"]
    service: str
    dependencies: dict[str, DependencyStatus]
