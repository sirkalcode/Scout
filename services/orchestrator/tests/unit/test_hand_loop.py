"""The hand-written agent loop, driven by scripted FakeClaude replies.

These answer "did my code do the right thing, given this reply?". Whether the real model
chooses well is a question for evals, from Day 21.
"""

from typing import Any

import pytest
from anthropic.lib.tools import ToolError
from pydantic import BaseModel, ConfigDict

from orchestrator.hand_loop.loop import run_agent
from orchestrator.hand_loop.sample_tools import SAMPLE_TOOLS
from orchestrator.hand_loop.tools import LocalTool
from scout_testkit import fake_claude as claude
from scout_testkit.fake_claude import FakeClaude, Step

from .conftest import client_for

QUESTION = "Should we worry about CVE-2021-44228?"
LOG4SHELL = {"cve_id": "CVE-2021-44228"}


def tool_results(fake: FakeClaude, request: int) -> list[dict[str, Any]]:
    """The tool_result blocks the loop sent in a given request."""
    last = fake.requests[request].body["messages"][-1]
    assert last["role"] == "user"
    return last["content"]


async def run(fake: FakeClaude, **options: Any) -> Any:
    return await run_agent(
        client_for(fake), model="claude-haiku-5-5", question=QUESTION, tools=SAMPLE_TOOLS, **options
    )


@pytest.mark.anyio
async def test_answers_using_both_tools() -> None:
    fake = FakeClaude(
        [
            claude.tool_use("check_kev", LOG4SHELL, tool_id="toolu_1"),
            claude.tool_use("lookup_cve", LOG4SHELL, tool_id="toolu_2"),
            claude.end_turn("Yes. Fix it first: it is actively exploited."),
        ]
    )

    result = await run(fake)

    assert result.answer == "Yes. Fix it first: it is actively exploited."
    assert result.stop_reason == "end_turn"
    assert result.steps == 3
    first, second = tool_results(fake, 1)[0], tool_results(fake, 2)[0]
    assert first["tool_use_id"] == "toolu_1"
    assert "is in" in first["content"]
    assert second["tool_use_id"] == "toolu_2"
    assert "Log4j" in second["content"]
    assert not first.get("is_error") and not second.get("is_error")


@pytest.mark.anyio
async def test_every_request_carries_the_whole_conversation() -> None:
    fake = FakeClaude([claude.tool_use("check_kev", LOG4SHELL), claude.end_turn("Done.")])

    result = await run(fake)

    roles = [message["role"] for message in fake.requests[1].body["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert [message["role"] for message in result.messages] == [*roles, "assistant"]


@pytest.mark.anyio
async def test_request_sends_strict_tools_and_no_sampling_parameters() -> None:
    fake = FakeClaude([claude.end_turn("Hi.")])

    await run(fake, max_tokens=700)

    body = fake.requests[0].body
    assert [tool["name"] for tool in body["tools"]] == ["lookup_cve", "check_kev"]
    assert all(tool["strict"] for tool in body["tools"])
    assert body["max_tokens"] == 700
    assert not {"temperature", "top_p", "top_k"} & body.keys()


@pytest.mark.anyio
async def test_several_tool_calls_in_one_reply_get_one_answer_in_order() -> None:
    both = Step(
        content=[
            claude.tool_use_block("lookup_cve", LOG4SHELL, tool_id="toolu_a"),
            claude.tool_use_block("check_kev", LOG4SHELL, tool_id="toolu_b"),
        ],
        stop_reason="tool_use",
    )
    fake = FakeClaude([both, claude.end_turn("Done.")])

    await run(fake)

    assert [block["tool_use_id"] for block in tool_results(fake, 1)] == ["toolu_a", "toolu_b"]


@pytest.mark.anyio
async def test_thinking_blocks_are_sent_back_unchanged() -> None:
    thinking = {"type": "thinking", "thinking": "Check KEV first.", "signature": "sig-123"}
    step = Step(
        content=[thinking, claude.tool_use_block("check_kev", LOG4SHELL)], stop_reason="tool_use"
    )
    fake = FakeClaude([step, claude.end_turn("Done.")])

    await run(fake)

    assistant = fake.requests[1].body["messages"][1]
    assert assistant["content"][0] == thinking


@pytest.mark.anyio
async def test_unknown_tool_is_reported_back_as_an_error() -> None:
    fake = FakeClaude([claude.tool_use("delete_tickets", {}), claude.end_turn("Sorry.")])

    await run(fake)

    result = tool_results(fake, 1)[0]
    assert result["is_error"] is True
    assert "delete_tickets" in result["content"]


@pytest.mark.anyio
async def test_invalid_input_is_reported_back_as_an_error() -> None:
    fake = FakeClaude([claude.tool_use("check_kev", {"cve_id": "log4j"}), claude.end_turn("Ok.")])

    await run(fake)

    result = tool_results(fake, 1)[0]
    assert result["is_error"] is True
    assert "cve_id" in result["content"]


@pytest.mark.anyio
async def test_tool_error_message_is_passed_to_the_model() -> None:
    unknown = {"cve_id": "CVE-2000-0000"}
    fake = FakeClaude([claude.tool_use("lookup_cve", unknown), claude.end_turn("Unknown.")])

    result = await run(fake)

    error = tool_results(fake, 1)[0]
    assert error["is_error"] is True
    assert "No record" in error["content"]
    assert result.answer == "Unknown."


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


@pytest.mark.anyio
async def test_unexpected_failures_are_reported_without_internal_details() -> None:
    async def broken() -> str:
        raise RuntimeError("password=hunter2 at /etc/secret")

    tool = LocalTool(name="broken", description="Always fails.", input_model=Empty, func=broken)
    fake = FakeClaude([claude.tool_use("broken", {}), claude.end_turn("Ok.")])

    await run_agent(client_for(fake), model="claude-haiku-5-5", question="Hi", tools=[tool])

    result = tool_results(fake, 1)[0]
    assert result["is_error"] is True
    assert "hunter2" not in result["content"]
    assert "RuntimeError" in result["content"]


@pytest.mark.anyio
async def test_expected_failures_use_tool_error() -> None:
    async def picky() -> str:
        raise ToolError("Try again later.")

    tool = LocalTool(name="picky", description="Fails politely.", input_model=Empty, func=picky)
    fake = FakeClaude([claude.tool_use("picky", {}), claude.end_turn("Ok.")])

    await run_agent(client_for(fake), model="claude-haiku-5-5", question="Hi", tools=[tool])

    assert tool_results(fake, 1)[0]["content"] == "Try again later."


@pytest.mark.anyio
async def test_max_steps_stops_an_agent_that_never_finishes() -> None:
    fake = FakeClaude([claude.tool_use("check_kev", LOG4SHELL)] * 3)

    result = await run(fake, max_steps=2)

    assert result.stop_reason == "max_steps"
    assert result.steps == 2
    assert len(fake.requests) == 2


@pytest.mark.anyio
async def test_pause_turn_continues_without_a_tool_result() -> None:
    fake = FakeClaude([claude.pause_turn(), claude.end_turn("Finished.")])

    result = await run(fake)

    assert result.answer == "Finished."
    assert fake.requests[1].body["messages"][-1]["role"] == "assistant"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("step", "stop_reason"),
    [(claude.max_tokens("Partial"), "max_tokens"), (claude.refusal(), "refusal")],
    ids=["max_tokens", "refusal"],
)
async def test_other_stop_reasons_end_the_run_and_are_reported(
    step: Step, stop_reason: str
) -> None:
    result = await run(FakeClaude([step]))

    assert result.stop_reason == stop_reason
    assert result.steps == 1


@pytest.mark.anyio
async def test_every_message_is_reported_as_it_happens() -> None:
    seen: list[str] = []
    fake = FakeClaude([claude.tool_use("check_kev", LOG4SHELL), claude.end_turn("Done.")])

    await run(fake, on_message=lambda message: seen.append(message["role"]))

    assert seen == ["user", "assistant", "user", "assistant"]


@pytest.mark.anyio
async def test_token_usage_is_added_up() -> None:
    usage = claude.TokenUsage(input_tokens=100, output_tokens=20)
    fake = FakeClaude(
        [
            claude.tool_use("check_kev", LOG4SHELL, usage=usage),
            claude.end_turn("Done.", usage=usage),
        ]
    )

    result = await run(fake)

    assert (result.input_tokens, result.output_tokens) == (200, 40)
