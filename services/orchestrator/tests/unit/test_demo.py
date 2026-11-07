"""The demo prints each message in a form a person can follow."""

import pytest
from anthropic.types import MessageParam

from orchestrator.hand_loop.demo import describe, run_demo
from scout_testkit import fake_claude as claude
from scout_testkit.fake_claude import FakeClaude

from .conftest import client_for


def test_question_and_answer_are_shown_in_full() -> None:
    assert describe({"role": "user", "content": "Should we worry?"}) == ["user: Should we worry?"]


def test_each_block_kind_gets_one_line() -> None:
    message: MessageParam = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "abc", "signature": "s"},
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "toolu_1", "name": "check_kev", "input": {"cve_id": "C"}},
        ],
    }

    assert describe(message) == [
        "assistant:",
        "  thinking (3 characters, hidden)",
        "  text: Checking.",
        '  tool_use check_kev {"cve_id": "C"} [id toolu_1]',
    ]


def test_errors_are_marked() -> None:
    message: MessageParam = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Bad.", "is_error": True},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "Good."},
        ],
    }

    assert describe(message) == [
        "user:",
        "  tool_result ERROR for toolu_1: Bad.",
        "  tool_result for toolu_2: Good.",
    ]


@pytest.mark.anyio
async def test_demo_prints_the_conversation_and_a_summary() -> None:
    fake = FakeClaude(
        [claude.tool_use("check_kev", {"cve_id": "CVE-2021-44228"}), claude.end_turn("Fix it.")]
    )
    lines: list[str] = []

    await run_demo(client_for(fake), "Worry?", model="claude-haiku-5-5", print_line=lines.append)

    text = "\n".join(lines)
    assert "1. user: Worry?" in text
    assert "tool_use check_kev" in text
    assert "tool_result for toolu_fake" in text
    assert "stop_reason: end_turn" in text
    assert "steps: 2" in text
