"""The agent loop: ask Claude, run the tools it asks for, send the results back, repeat."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.lib.tools import ToolError
from anthropic.types import Message, MessageParam, ToolResultBlockParam, ToolUseBlock
from pydantic import ValidationError

from .tools import LocalTool

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 8
# Thinking counts toward max_tokens, so leave room for it (see ADR 0001, finding 4).
DEFAULT_MAX_TOKENS = 1024

OnMessage = Callable[[MessageParam], object]


@dataclass
class AgentResult:
    answer: str
    stop_reason: str  # Claude's last stop reason, or "max_steps"
    steps: int  # how many times Claude was called
    messages: list[MessageParam]
    input_tokens: int = 0
    output_tokens: int = 0


def _error(tool_use: ToolUseBlock, message: str) -> ToolResultBlockParam:
    return {"type": "tool_result", "tool_use_id": tool_use.id, "content": message, "is_error": True}


async def _run_tool(tools: dict[str, LocalTool], tool_use: ToolUseBlock) -> ToolResultBlockParam:
    """Never raises. Every failure goes back to Claude as is_error, so it can change course."""
    tool = tools.get(tool_use.name)
    if tool is None:
        return _error(tool_use, f"There is no tool named {tool_use.name}.")
    try:
        content = await tool.call(tool_use.input)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, item['loc'])) or 'input'}: {item['msg']}"
            for item in error.errors()
        )
        return _error(tool_use, f"Invalid input for {tool.name}: {problems}")
    except ToolError as error:
        # An expected failure, written for the model to read.
        return _error(tool_use, str(error.content))
    except Exception as error:
        # An unexpected failure. Log the details here and tell the model only what kind it was.
        logger.exception("Tool %s failed", tool.name)
        return _error(tool_use, f"{tool.name} failed unexpectedly ({type(error).__name__}).")
    return {"type": "tool_result", "tool_use_id": tool_use.id, "content": content}


def _text(reply: Message) -> str:
    return "".join(block.text for block in reply.content if block.type == "text")


async def run_agent(
    client: AsyncAnthropic,
    *,
    model: str,
    question: str,
    tools: Sequence[LocalTool],
    system: str | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    on_message: OnMessage | None = None,
) -> AgentResult:
    by_name = {tool.name: tool for tool in tools}
    definitions = [tool.definition() for tool in tools]
    extra: dict[str, Any] = {"system": system} if system else {}
    messages: list[MessageParam] = []
    result = AgentResult(answer="", stop_reason="max_steps", steps=0, messages=messages)

    def add(message: MessageParam) -> None:
        messages.append(message)
        if on_message is not None:
            on_message(message)

    add({"role": "user", "content": question})
    while result.steps < max_steps:
        reply = await client.messages.create(
            model=model, max_tokens=max_tokens, messages=messages, tools=definitions, **extra
        )
        result.steps += 1
        result.input_tokens += reply.usage.input_tokens
        result.output_tokens += reply.usage.output_tokens
        # Send Claude's reply back unchanged, including thinking blocks and their signatures.
        add({"role": "assistant", "content": [block.to_dict() for block in reply.content]})  # type: ignore[misc]

        if reply.stop_reason == "tool_use":
            tool_uses = [block for block in reply.content if block.type == "tool_use"]
            add({"role": "user", "content": [await _run_tool(by_name, use) for use in tool_uses]})
        elif reply.stop_reason == "pause_turn":
            continue  # Claude paused a long turn. Sending the conversation back lets it go on.
        else:
            result.answer = _text(reply)
            result.stop_reason = str(reply.stop_reason)
            return result
    return result
