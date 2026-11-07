"""Builds an Anthropic SDK client that talks to FakeClaude instead of the network."""

import httpx2
import pytest
from anthropic import AsyncAnthropic

from scout_testkit.fake_claude import FakeClaude


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def client_for(fake: FakeClaude) -> AsyncAnthropic:
    return AsyncAnthropic(
        api_key="test-token",
        base_url="http://llm-gateway:8000",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=fake),
    )
