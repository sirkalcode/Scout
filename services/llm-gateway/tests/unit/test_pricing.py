"""Rates come from https://platform.claude.com/docs/en/about-claude/pricing, read 2026-11-05."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from llm_gateway.pricing import UnknownModel, cost_usd, worst_case_cost_usd
from llm_gateway.usage import Usage

M = 1_000_000


@pytest.mark.parametrize(
    ("model", "field", "dollars"),
    [
        ("claude-opus-5-5", "input_tokens", "4"),
        ("claude-opus-5-5", "output_tokens", "20"),
        ("claude-opus-5-5", "cache_read_input_tokens", "0.20"),
        ("claude-opus-5-5", "cache_write_5m_tokens", "5"),
        ("claude-opus-5-5", "cache_write_1h_tokens", "8"),
        ("claude-sonnet-5-5", "input_tokens", "2"),
        ("claude-sonnet-5-5", "output_tokens", "10"),
        ("claude-sonnet-5-5", "cache_read_input_tokens", "0.10"),
        ("claude-sonnet-5-5", "cache_write_5m_tokens", "2.50"),
        ("claude-sonnet-5-5", "cache_write_1h_tokens", "4"),
    ],
)
def test_published_rates(model: str, field: str, dollars: str) -> None:
    assert cost_usd(model, Usage(**{field: M})) == Decimal(dollars)


@pytest.mark.parametrize(
    ("usage", "dollars"),
    [
        (Usage(input_tokens=100_000), "0.01"),
        (Usage(input_tokens=100_000, output_tokens=M), "0.51"),
        (Usage(input_tokens=100_001), "0.0500005"),
        (Usage(input_tokens=1, cache_read_input_tokens=100_000), "0.0050005"),
        (Usage(input_tokens=1, cache_write_5m_tokens=100_000), "0.0625005"),
        (Usage(input_tokens=1, cache_write_1h_tokens=100_000), "0.1000005"),
        (Usage(input_tokens=100_001, output_tokens=M), "2.5500005"),
    ],
    ids=[
        "at-limit",
        "at-limit-output",
        "over",
        "cache-read",
        "write-5m",
        "write-1h",
        "over-output",
    ],
)
def test_haiku_tier_counts_every_input_token(usage: Usage, dollars: str) -> None:
    assert cost_usd("claude-haiku-5-5", usage) == Decimal(dollars)


def test_web_search_is_charged_per_search() -> None:
    assert cost_usd("claude-sonnet-5-5", Usage(web_search_requests=3)) == Decimal("0.03")


def test_unknown_models_cannot_be_priced() -> None:
    with pytest.raises(UnknownModel):
        cost_usd("claude-made-up", Usage(input_tokens=1))


def test_worst_case_covers_the_full_output_and_the_top_tier() -> None:
    assert worst_case_cost_usd("claude-sonnet-5-5", max_tokens=M, body_bytes=0) == Decimal("10")
    assert worst_case_cost_usd("claude-haiku-5-5", max_tokens=M, body_bytes=0) == Decimal("2.5")


@given(
    input_tokens=st.integers(0, 10**7),
    output_tokens=st.integers(0, 10**6),
    extra=st.integers(0, 10**6),
)
def test_more_output_never_costs_less(input_tokens: int, output_tokens: int, extra: int) -> None:
    usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
    more = Usage(input_tokens=input_tokens, output_tokens=output_tokens + extra)

    assert Decimal(0) <= cost_usd("claude-opus-5-5", usage) <= cost_usd("claude-opus-5-5", more)
