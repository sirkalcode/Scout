"""The SDK's Tool Runner does the same job as the hand-written loop.

Same tools, same scripted replies: the requests Claude would see should match.
"""

import pytest
from orchestrator.hand_loop.loop import run_agent
from orchestrator.hand_loop.sample_tools import SAMPLE_TOOLS
from orchestrator.hand_loop.tool_runner import run_with_tool_runner

from scout_testkit import fake_claude as claude
from scout_testkit.fake_claude import FakeClaude, Step

from .conftest import client_for

LOG4SHELL = {"cve_id": "CVE-2021-44228"}


def script() -> list[Step]:
    return [
        claude.tool_use("check_kev", LOG4SHELL, tool_id="toolu_1"),
        claude.tool_use("lookup_cve", {"cve_id": "CVE-2000-0000"}, tool_id="toolu_2"),
        claude.end_turn("Fix it first."),
    ]


@pytest.mark.anyio
async def test_both_engines_send_the_same_conversation() -> None:
    by_hand, by_runner = FakeClaude(script()), FakeClaude(script())
    options = {"model": "claude-haiku-5-5", "question": "Worry?", "tools": SAMPLE_TOOLS}

    hand = await run_agent(client_for(by_hand), **options)
    runner = await run_with_tool_runner(client_for(by_runner), **options)

    assert hand.answer == runner == "Fix it first."
    for left, right in zip(by_hand.requests, by_runner.requests, strict=True):
        assert left.body["tools"] == right.body["tools"]
        assert left.body["messages"] == right.body["messages"]


@pytest.mark.anyio
async def test_tool_runner_respects_the_step_limit() -> None:
    fake = FakeClaude([claude.tool_use("check_kev", LOG4SHELL)] * 3)

    await run_with_tool_runner(
        client_for(fake),
        model="claude-haiku-5-5",
        question="Worry?",
        tools=SAMPLE_TOOLS,
        max_steps=2,
    )

    assert len(fake.requests) == 2
