"""Pins what ChatAnthropic sends and parses, so a framework upgrade cannot change it silently.

Background and results are in docs/adr/0001-langchain-anthropic-compatibility.md.
"""

import anthropic
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from orchestrator.llm import DEFAULT_MAX_TOKENS, build_chat_model
from orchestrator.settings import Settings
from pydantic import SecretStr

from .conftest import FakeAnthropicApi

pytestmark = pytest.mark.enable_socket

SAMPLING_PARAMETERS = {"temperature", "top_p", "top_k"}


@tool
def get_kev(cve: str) -> str:
    """Look up a CVE in the Known Exploited Vulnerabilities catalog."""
    return cve


def model_for(api: FakeAnthropicApi, model: str = "claude-sonnet-5-5") -> ChatAnthropic:
    settings = Settings(llm_gateway_url=api.url, llm_gateway_token=SecretStr("test-token"))
    return build_chat_model(settings, model=model)


def test_stack_uses_the_new_sdk_major_version() -> None:
    assert int(anthropic.__version__.split(".")[0]) >= 2


def test_requests_go_to_the_gateway_with_the_new_api_version(fake_api: FakeAnthropicApi) -> None:
    model_for(fake_api).invoke("hello")

    assert fake_api.last.path == "/v1/messages"
    assert fake_api.last.headers["anthropic-version"] == "2026-10-21"


def test_key_is_sent_as_a_bearer_token(fake_api: FakeAnthropicApi) -> None:
    model_for(fake_api).invoke("hello")

    assert fake_api.last.headers["authorization"] == "Bearer test-token"
    assert "x-api-key" not in fake_api.last.headers


@pytest.mark.parametrize("name", ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"])
def test_no_sampling_parameters_are_sent(fake_api: FakeAnthropicApi, name: str) -> None:
    model_for(fake_api, model=name).bind_tools([get_kev]).invoke("hello")

    assert not SAMPLING_PARAMETERS & fake_api.last.body.keys()


def test_max_tokens_is_set_explicitly(fake_api: FakeAnthropicApi) -> None:
    model_for(fake_api).invoke("hello")

    assert fake_api.last.body["max_tokens"] == DEFAULT_MAX_TOKENS


def test_tool_calls_are_parsed(fake_api: FakeAnthropicApi) -> None:
    result = model_for(fake_api).bind_tools([get_kev]).invoke("hello")

    assert isinstance(result, AIMessage)
    assert [call["name"] for call in result.tool_calls] == ["get_kev"]
    assert result.tool_calls[0]["args"] == {"cve": "CVE-2021-44228"}
    assert fake_api.last.body["tools"][0]["name"] == "get_kev"


def test_stop_reason_survives_the_wrapper(fake_api: FakeAnthropicApi) -> None:
    result = model_for(fake_api).invoke("hello")

    assert result.response_metadata["stop_reason"] == "tool_use"


def test_cache_usage_survives_but_input_tokens_are_summed(fake_api: FakeAnthropicApi) -> None:
    usage = model_for(fake_api).invoke("hello").usage_metadata

    assert usage is not None
    assert usage.get("input_token_details") == {"cache_read": 7, "cache_creation": 3}
    # LangChain adds cached tokens to input_tokens, unlike the API's own field (10).
    # Cost accounting therefore belongs in llm-gateway, which reads the raw usage.
    assert usage["input_tokens"] == 20


def test_streaming_text_and_stop_reason_arrive(fake_api: FakeAnthropicApi) -> None:
    chunks = list(model_for(fake_api).stream("hello"))

    text = "".join(chunk.text for chunk in chunks)
    assert text == "Patch first."
    assert fake_api.last.body["stream"] is True
    assert any(chunk.response_metadata.get("stop_reason") == "end_turn" for chunk in chunks)


@pytest.mark.parametrize("name", ["claude-haiku-5-5", "claude-opus-5-5"])
def test_direct_chat_anthropic_can_still_leak_temperature(
    fake_api: FakeAnthropicApi, name: str
) -> None:
    """Why build_chat_model takes no sampling arguments, and why llm-gateway must reject them.

    API version 2026-10-21 answers these requests with a 400. If this test starts failing
    because LangChain blocks them itself, that is good news: update the ADR.
    """
    model = ChatAnthropic(
        model_name=name,
        base_url=fake_api.url,
        api_key=SecretStr("test-token"),
        max_tokens_to_sample=10,
        temperature=0.0,
        timeout=None,
        stop=None,
    )

    model.invoke("hello")

    assert fake_api.last.body["temperature"] == 0.0


def test_build_chat_model_refuses_sampling_arguments(fake_api: FakeAnthropicApi) -> None:
    settings = Settings(llm_gateway_url=fake_api.url, llm_gateway_token=SecretStr("t"))

    with pytest.raises(TypeError):
        build_chat_model(settings, model="claude-haiku-5-5", temperature=0.0)  # type: ignore[call-arg]
