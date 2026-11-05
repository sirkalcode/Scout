"""Orchestrator configuration, read from the environment."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_gateway_url: str = "http://llm-gateway:8000"
    # An internal token for llm-gateway. The real Claude key lives only in that service.
    llm_gateway_token: SecretStr = SecretStr("local-dev-token")
