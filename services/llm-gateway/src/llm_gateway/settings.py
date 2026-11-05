"""llm-gateway configuration, read from the environment."""

from decimal import Decimal
from pathlib import Path
from typing import Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings

from scout_contracts.llm import GatewayMode


class Settings(BaseSettings):
    llm_gateway_mode: GatewayMode = "fake"
    llm_gateway_token: SecretStr = SecretStr("local-dev-token")
    llm_daily_limit_usd: Decimal = Decimal(15)
    llm_gateway_fake_script: Path | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_version: str = "2026-10-21"
    database_url: SecretStr | None = None

    @field_validator("anthropic_api_key", "database_url", mode="after")
    @classmethod
    def blank_means_missing(cls, value: SecretStr | None) -> SecretStr | None:
        return value if value and value.get_secret_value().strip() else None

    @model_validator(mode="after")
    def real_mode_is_fully_configured(self) -> Self:
        if self.llm_gateway_mode == "real":
            if self.anthropic_api_key is None:
                raise ValueError("Real mode needs ANTHROPIC_API_KEY")
            if self.database_url is None:
                raise ValueError(
                    "Real mode needs DATABASE_URL, so the daily limit survives restarts"
                )
        return self
