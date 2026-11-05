"""A real-mode gateway must not start half-configured."""

import pytest
from llm_gateway.settings import Settings
from pydantic import SecretStr, ValidationError


def test_fake_mode_needs_no_key_or_database() -> None:
    settings = Settings(llm_gateway_mode="fake", anthropic_api_key=None, database_url=None)

    assert settings.llm_gateway_mode == "fake"


@pytest.mark.parametrize(
    "missing",
    [
        {"anthropic_api_key": None, "database_url": "postgresql+asyncpg://x"},
        {"anthropic_api_key": SecretStr("k"), "database_url": None},
    ],
    ids=["no-key", "no-database"],
)
def test_real_mode_requires_key_and_database(missing: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(llm_gateway_mode="real", **missing)  # type: ignore[arg-type]


def test_blank_key_counts_as_missing() -> None:
    with pytest.raises(ValidationError):
        Settings(
            llm_gateway_mode="real",
            anthropic_api_key=SecretStr(""),
            database_url="postgresql+asyncpg://x",
        )


def test_secrets_do_not_appear_in_repr() -> None:
    settings = Settings(llm_gateway_mode="fake", anthropic_api_key=SecretStr("real-claude-key"))

    assert "real-claude-key" not in repr(settings)
