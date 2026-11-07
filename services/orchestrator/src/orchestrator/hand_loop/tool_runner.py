"""The same agent on the SDK's Tool Runner, which runs the loop for you.

Compare it with loop.py: the runner replaces the while loop, the tool dispatch and the error
handling. See docs/learning/day-3-agent-loop.md.
"""

from collections.abc import Sequence
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.lib.tools import beta_async_tool

from .loop import DEFAULT_MAX_STEPS, DEFAULT_MAX_TOKENS
from .tools import LocalTool


async def run_with_tool_runner(
    client: AsyncAnthropic,
    *,
    model: str,
    question: str,
    tools: Sequence[LocalTool],
    max_steps: int = DEFAULT_MAX_STEPS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    use_thread: bool = False,
) -> str:
    """With use_thread, the runner uses the message-threads beta: Anthropic stores the
    conversation, and each later request sends only the new tool results. Without it, every
    request resends the whole conversation, exactly like loop.py."""
    runnable = [
        beta_async_tool(
            tool.func,
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema(),
            strict=True,
        )
        for tool in tools
    ]
    options: dict[str, Any] = {} if use_thread else {"thread": False}
    runner = client.beta.messages.tool_runner(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": question}],
        tools=runnable,
        max_iterations=max_steps,
        **options,
    )
    final = await runner.until_done()
    return "".join(block.text for block in final.content if block.type == "text")
