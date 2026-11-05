"""One behavior suite that every SpendLedger implementation must pass."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from llm_gateway.ledger import DaySpend, SpendLedger, SpendRecord
from llm_gateway.usage import Usage

DAY = date(2026, 11, 5)


def record(cost: str, day: date = DAY) -> SpendRecord:
    return SpendRecord(
        created_at=datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        request_id="req-1",
        model="claude-sonnet-5-5",
        usage=Usage(input_tokens=10, output_tokens=5, cache_read_input_tokens=7),
        cost_usd=Decimal(cost),
        estimated=False,
    )


class LedgerContract:
    @pytest.mark.anyio
    async def test_empty_day_has_no_spend(self, ledger: SpendLedger) -> None:
        assert await ledger.spent_on(DAY) == DaySpend(Decimal(0), 0)

    @pytest.mark.anyio
    async def test_records_add_up_exactly(self, ledger: SpendLedger) -> None:
        await ledger.record(record("0.00000350"))
        await ledger.record(record("0.10000000"))

        assert await ledger.spent_on(DAY) == DaySpend(Decimal("0.10000350"), 2)

    @pytest.mark.anyio
    async def test_other_days_are_not_counted(self, ledger: SpendLedger) -> None:
        await ledger.record(record("1", day=date(2026, 11, 4)))
        await ledger.record(record("2", day=DAY))

        assert await ledger.spent_on(DAY) == DaySpend(Decimal(2), 1)
