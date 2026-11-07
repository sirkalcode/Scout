"""Runs the Day 3 agent through llm-gateway and prints every message it sends and receives.

    python -m orchestrator.hand_loop.demo ["your question"] [--model claude-sonnet-5-5]

In fake mode it costs nothing. In real mode one run costs well under one cent with Haiku.
"""

import argparse
import asyncio
import json
from collections.abc import Callable
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from ..live_check import _spend  # pyright: ignore[reportPrivateUsage]
from ..settings import Settings
from .loop import run_agent
from .sample_tools import SAMPLE_TOOLS

DEFAULT_QUESTION = (
    "We run an internet-facing Java service that uses Log4j, and CVE-2021-44228 came up in a "
    "scan. Should we fix it first? Use your tools."
)
DEFAULT_MODEL = "claude-haiku-5-5"
SYSTEM = (
    "You help people decide which security flaws to fix first. Use the tools for facts. "
    "Answer in two or three sentences and say which tool results you relied on."
)


def _block_line(block: dict[str, Any]) -> str:
    match block.get("type"):
        case "text":
            return f"text: {block['text']}"
        case "thinking":
            return f"thinking ({len(block.get('thinking', ''))} characters, hidden)"
        case "tool_use":
            return f"tool_use {block['name']} {json.dumps(block['input'])} [id {block['id']}]"
        case "tool_result":
            label = "tool_result ERROR" if block.get("is_error") else "tool_result"
            return f"{label} for {block['tool_use_id']}: {block.get('content')}"
        case other:
            return f"{other} block"


def describe(message: MessageParam) -> list[str]:
    content = message["content"]
    if isinstance(content, str):
        return [f"{message['role']}: {content}"]
    return [f"{message['role']}:", *(f"  {_block_line(dict(block))}" for block in content)]


async def run_demo(
    client: AsyncAnthropic,
    question: str,
    *,
    model: str,
    print_line: Callable[[str], object] = print,
) -> None:
    number = 0

    def show(message: MessageParam) -> None:
        nonlocal number
        number += 1
        first, *rest = describe(message)
        print_line(f"{number}. {first}")
        for line in rest:
            print_line(f"   {line}")

    result = await run_agent(
        client, model=model, question=question, tools=SAMPLE_TOOLS, system=SYSTEM, on_message=show
    )
    print_line("")
    print_line(f"stop_reason: {result.stop_reason}")
    print_line(f"steps: {result.steps} (calls to Claude)")
    print_line(f"tokens: {result.input_tokens} input, {result.output_tokens} output")


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    settings = Settings()
    client = AsyncAnthropic(
        base_url=settings.llm_gateway_url,
        api_key=settings.llm_gateway_token.get_secret_value(),
        max_retries=0,  # llm-gateway already retries
    )
    before = _spend(settings)
    await run_demo(client, args.question, model=args.model)
    after = _spend(settings)
    print(f"mode: {after.mode}, cost: ${after.spent_usd - before.spent_usd}")


if __name__ == "__main__":
    asyncio.run(_main())
