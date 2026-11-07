"""Makes one normal and one streaming call through llm-gateway, then prints a JSON report.

In fake mode this costs nothing. In real mode it uses the cheapest model and a tiny token limit.
"""

import json
from typing import Any

import httpx2

from scout_contracts.llm import SpendToday

from .llm import build_chat_model
from .settings import Settings

MODEL = "claude-haiku-5-5"
# Thinking tokens count toward max_tokens. A live call with 32 spent them all on thinking.
MAX_TOKENS = 512
PROMPT = "Say hello in one short sentence."


def _spend(settings: Settings) -> SpendToday:
    token = settings.llm_gateway_token.get_secret_value()
    response = httpx2.get(
        f"{settings.llm_gateway_url}/v1/spend/today",
        headers={"authorization": f"Bearer {token}"},
        timeout=10,
        trust_env=False,
    )
    response.raise_for_status()
    return SpendToday.model_validate_json(response.content)


def run(settings: Settings) -> dict[str, Any]:
    model = build_chat_model(settings, model=MODEL, max_tokens=MAX_TOKENS)
    before = _spend(settings)

    answer = model.invoke(PROMPT)

    merged = None
    for chunk in model.stream(PROMPT):
        merged = chunk if merged is None else merged + chunk
    if merged is None:
        raise RuntimeError("The stream returned nothing")

    after = _spend(settings)
    return {
        "mode": after.mode,
        "model": MODEL,
        "invoke": {
            "text": answer.text,
            "stop_reason": answer.response_metadata.get("stop_reason"),
            "usage_metadata": answer.usage_metadata,
            "raw_usage": answer.response_metadata.get("usage"),
        },
        "stream": {
            "text": merged.text,
            "stop_reason": merged.response_metadata.get("stop_reason"),
            "usage_metadata": merged.usage_metadata,
        },
        "ledger": {
            "requests_added": after.requests - before.requests,
            "cost_added_usd": str(after.spent_usd - before.spent_usd),
            "spent_today_usd": str(after.spent_usd),
            "limit_usd": str(after.limit_usd),
        },
    }


def main() -> None:
    print(json.dumps(run(Settings()), indent=2, default=str))


if __name__ == "__main__":
    main()
