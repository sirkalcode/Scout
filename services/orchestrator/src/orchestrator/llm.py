"""The one place agents get a chat model.

There is deliberately no way to pass temperature, top_p or top_k: API version 2026-10-21
rejects them, and LangChain does not block them for every model. See
docs/adr/0001-langchain-anthropic-compatibility.md.
"""

from langchain_anthropic import ChatAnthropic

from orchestrator.settings import Settings

# Set explicitly, because LangChain otherwise uses a very large per-model default.
DEFAULT_MAX_TOKENS = 4096


def build_chat_model(
    settings: Settings,
    *,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> ChatAnthropic:
    return ChatAnthropic(
        model_name=model,
        base_url=settings.llm_gateway_url,
        api_key=settings.llm_gateway_token,
        max_tokens_to_sample=max_tokens,
        # llm-gateway owns retries, so they are not multiplied by a second layer here.
        max_retries=0,
        timeout=None,
        stop=None,
    )
