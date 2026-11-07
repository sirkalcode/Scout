"""The two local tools behind the Day 3 agent, tested without any model."""

import pytest
from anthropic.lib.tools import ToolError
from pydantic import ValidationError

from orchestrator.hand_loop.sample_tools import (
    CHECK_KEV,
    LOOKUP_CVE,
    SAMPLE_TOOLS,
    CveInput,
    check_kev,
    lookup_cve,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_check_kev_reports_a_listed_flaw() -> None:
    assert "is in" in await check_kev(cve_id="CVE-2021-44228")


@pytest.mark.anyio
async def test_check_kev_reports_an_unlisted_flaw() -> None:
    assert "is not in" in await check_kev(cve_id="CVE-2099-0001")


@pytest.mark.anyio
async def test_lookup_cve_describes_a_known_flaw() -> None:
    text = await lookup_cve(cve_id="CVE-2021-44228")

    assert "Log4j" in text
    assert "10.0" in text


@pytest.mark.anyio
async def test_lookup_cve_raises_a_tool_error_for_an_unknown_flaw() -> None:
    with pytest.raises(ToolError, match="No record"):
        await lookup_cve(cve_id="CVE-2000-0000")


@pytest.mark.parametrize("bad", ["log4j", "CVE-21-1", "cve-2021-44228", "CVE-2021-44228 OR 1=1"])
def test_input_model_rejects_malformed_ids(bad: str) -> None:
    with pytest.raises(ValidationError):
        CveInput.model_validate({"cve_id": bad})


def test_input_model_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CveInput.model_validate({"cve_id": "CVE-2021-44228", "admin": True})


@pytest.mark.parametrize("tool", SAMPLE_TOOLS, ids=lambda tool: tool.name)
def test_definitions_are_strict_and_closed(tool: object) -> None:
    definition = tool.definition()  # type: ignore[attr-defined]
    schema = definition["input_schema"]

    assert definition["strict"] is True
    assert definition["description"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["cve_id"]
    # Strict mode does not support `pattern`, so the SDK moves it into the description.
    assert "pattern" not in schema["properties"]["cve_id"]


def test_the_two_tools_are_named_for_the_model() -> None:
    assert [tool.name for tool in SAMPLE_TOOLS] == [LOOKUP_CVE.name, CHECK_KEV.name]
    assert {tool.name for tool in SAMPLE_TOOLS} == {"lookup_cve", "check_kev"}
