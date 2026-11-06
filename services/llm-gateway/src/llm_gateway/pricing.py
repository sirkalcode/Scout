"""Claude prices in US dollars per million tokens.

Source: https://platform.claude.com/docs/en/about-claude/pricing, read on 2026-11-05.
Update this file and tests/unit/test_pricing.py together when prices change.
"""

from dataclasses import dataclass
from decimal import Decimal

from .usage import Usage

MILLION = Decimal(1_000_000)
WEB_SEARCH_USD = Decimal("0.01")
HAIKU_TIER_LIMIT = 100_000  # Counts every input token, including cache reads and writes.
BODY_BYTES_PER_TOKEN_ESTIMATE = 2


class UnknownModel(ValueError):
    pass


@dataclass(frozen=True)
class Rates:
    input: Decimal
    write_5m: Decimal
    write_1h: Decimal
    read: Decimal
    output: Decimal


def _rates(input_: str, write_5m: str, write_1h: str, read: str, output: str) -> Rates:
    return Rates(
        Decimal(input_), Decimal(write_5m), Decimal(write_1h), Decimal(read), Decimal(output)
    )


OPUS = _rates("4", "5", "8", "0.20", "20")
SONNET = _rates("2", "2.50", "4", "0.10", "10")
HAIKU_SMALL = _rates("0.10", "0.125", "0.20", "0.01", "0.50")
HAIKU_LARGE = _rates("0.50", "0.625", "1", "0.05", "2.50")

HIGHEST_RATES = {
    "claude-opus-5-5": OPUS,
    "claude-sonnet-5-5": SONNET,
    "claude-haiku-5-5": HAIKU_LARGE,
}


def is_priced(model: str) -> bool:
    return model in HIGHEST_RATES


def _rates_for(model: str, usage: Usage) -> Rates:
    if model == "claude-haiku-5-5" and usage.total_input_tokens <= HAIKU_TIER_LIMIT:
        return HAIKU_SMALL
    if model in HIGHEST_RATES:
        return HIGHEST_RATES[model]
    raise UnknownModel(model)


def cost_usd(model: str, usage: Usage) -> Decimal:
    rates = _rates_for(model, usage)
    per_million = (
        usage.input_tokens * rates.input
        + usage.cache_write_5m_tokens * rates.write_5m
        + usage.cache_write_1h_tokens * rates.write_1h
        + usage.cache_read_input_tokens * rates.read
        + usage.output_tokens * rates.output
    )
    return per_million / MILLION + usage.web_search_requests * WEB_SEARCH_USD


def worst_case_cost_usd(model: str, *, max_tokens: int, body_bytes: int) -> Decimal:
    """The most this request can reasonably cost, reserved before the call is made."""
    if model not in HIGHEST_RATES:
        raise UnknownModel(model)
    rates = HIGHEST_RATES[model]
    input_tokens = body_bytes // BODY_BYTES_PER_TOKEN_ESTIMATE
    return (input_tokens * rates.write_1h + max_tokens * rates.output) / MILLION
