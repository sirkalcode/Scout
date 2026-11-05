"""Gateway configuration, read from the environment."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    orchestrator_url: str = "http://orchestrator:8000"
    dependency_timeout_seconds: float = 2.0
