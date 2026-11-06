"""The daily limit: reservations, settlement, day rollover and ledger failures."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from llm_gateway.budget import BudgetExceeded, BudgetGuard
from llm_gateway.ledger import DaySpend, InMemoryLedger, LedgerError, SpendRecord
from llm_gateway.usage import Usage


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


NOW = datetime(2026, 11, 5, 23, 59, tzinfo=UTC)
TOMORROW = datetime(2026, 11, 6, 0, 1, tzinfo=UTC)


def spend(cost: str, when: datetime = NOW) -> SpendRecord:
    return SpendRecord(
        created_at=when,
        request_id="r",
        model="claude-sonnet-5-5",
        usage=Usage(),
        cost_usd=Decimal(cost),
        estimated=False,
    )


class BrokenLedger(InMemoryLedger):
    fail_reads = False
    fail_writes = False

    async def spent_on(self, day):  # type: ignore[no-untyped-def]
        if self.fail_reads:
            raise LedgerError("down")
        return await super().spent_on(day)

    async def record(self, record: SpendRecord) -> None:
        if self.fail_writes:
            raise LedgerError("down")
        await super().record(record)


def guard(limit: str, ledger: InMemoryLedger | None = None, now: datetime = NOW) -> BudgetGuard:
    return BudgetGuard(ledger or InMemoryLedger(), Decimal(limit), clock=lambda: now)


@pytest.mark.anyio
async def test_reserve_within_the_limit_succeeds() -> None:
    await guard("1").reserve(Decimal("1"))


@pytest.mark.anyio
async def test_reserve_past_the_limit_is_refused() -> None:
    with pytest.raises(BudgetExceeded):
        await guard("1").reserve(Decimal("1.01"))


@pytest.mark.anyio
async def test_in_flight_reservations_count_against_the_limit() -> None:
    budget = guard("1")
    await budget.reserve(Decimal("0.6"))

    with pytest.raises(BudgetExceeded):
        await budget.reserve(Decimal("0.6"))


@pytest.mark.anyio
async def test_settling_replaces_the_reservation_with_the_actual_cost() -> None:
    ledger = InMemoryLedger()
    budget = guard("1", ledger)
    reservation = await budget.reserve(Decimal("0.9"))

    await budget.settle(reservation, spend("0.1"))

    await budget.reserve(Decimal("0.9"))
    assert await ledger.spent_on(NOW.date()) == DaySpend(Decimal("0.1"), 1)


@pytest.mark.anyio
async def test_settling_with_no_cost_just_releases() -> None:
    budget = guard("1")
    reservation = await budget.reserve(Decimal("1"))

    await budget.settle(reservation, None)

    await budget.reserve(Decimal("1"))


@pytest.mark.anyio
async def test_settling_twice_records_once() -> None:
    ledger = InMemoryLedger()
    budget = guard("1", ledger)
    reservation = await budget.reserve(Decimal("0.5"))

    await budget.settle(reservation, spend("0.1"))
    await budget.settle(reservation, spend("0.1"))

    assert (await ledger.spent_on(NOW.date())).requests == 1


@pytest.mark.anyio
async def test_past_spend_counts_against_the_limit() -> None:
    ledger = InMemoryLedger()
    await ledger.record(spend("0.9"))

    with pytest.raises(BudgetExceeded):
        await guard("1", ledger).reserve(Decimal("0.2"))


@pytest.mark.anyio
async def test_a_new_utc_day_starts_fresh() -> None:
    ledger = InMemoryLedger()
    await ledger.record(spend("1"))

    await guard("1", ledger, now=TOMORROW).reserve(Decimal("1"))


@pytest.mark.anyio
async def test_refuses_when_spend_cannot_be_read() -> None:
    ledger = BrokenLedger()
    ledger.fail_reads = True

    with pytest.raises(LedgerError):
        await guard("1", ledger).reserve(Decimal("0.1"))


@pytest.mark.anyio
async def test_failed_write_still_counts_against_the_limit() -> None:
    ledger = BrokenLedger()
    budget = guard("1", ledger)
    reservation = await budget.reserve(Decimal("0.9"))
    ledger.fail_writes = True

    await budget.settle(reservation, spend("0.9"))

    with pytest.raises(BudgetExceeded):
        await budget.reserve(Decimal("0.2"))


@pytest.mark.anyio
async def test_summary_reports_spend_and_remaining() -> None:
    ledger = InMemoryLedger()
    await ledger.record(spend("0.25"))

    summary = await guard("1", ledger).summary()

    assert summary.spent_usd == Decimal("0.25")
    assert summary.remaining_usd == Decimal("0.75")
    assert summary.limit_usd == Decimal("1")
    assert summary.requests == 1
