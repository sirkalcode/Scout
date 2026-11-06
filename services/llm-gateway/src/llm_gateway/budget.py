"""The hard daily limit. Every call reserves its worst case before it runs.

This keeps state in one process, so llm-gateway must run as a single replica. See ADR 0002.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from .ledger import LedgerError, SpendLedger, SpendRecord

logger = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    pass


class Reservation:
    def __init__(self, amount: Decimal) -> None:
        self.amount = amount


@dataclass(frozen=True)
class BudgetSummary:
    day: date
    limit_usd: Decimal
    spent_usd: Decimal
    remaining_usd: Decimal
    requests: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BudgetGuard:
    def __init__(
        self,
        ledger: SpendLedger,
        limit_usd: Decimal,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._ledger = ledger
        self._limit = limit_usd
        self._clock = clock
        self._lock = asyncio.Lock()
        self._active: set[Reservation] = set()
        self._unsaved: dict[date, Decimal] = {}

    def _today(self) -> date:
        return self._clock().astimezone(UTC).date()

    async def reserve(self, amount: Decimal) -> Reservation:
        """Raises BudgetExceeded over the limit, and LedgerError if spend cannot be read."""
        async with self._lock:
            today = self._today()
            spent = (await self._ledger.spent_on(today)).total_usd
            held = sum((item.amount for item in self._active), Decimal(0))
            if spent + self._unsaved.get(today, Decimal(0)) + held + amount > self._limit:
                raise BudgetExceeded(f"Daily limit of ${self._limit} would be passed")
            reservation = Reservation(amount)
            self._active.add(reservation)
            return reservation

    async def settle(self, reservation: Reservation, record: SpendRecord | None) -> None:
        """Replaces a reservation with what was really spent. A second call does nothing."""
        async with self._lock:
            if reservation not in self._active:
                return
            self._active.discard(reservation)
            if record is None:
                return
            try:
                await self._ledger.record(record)
            except LedgerError:
                # The money is spent either way, so it still counts until this process restarts.
                logger.exception("Could not save spend of $%s", record.cost_usd)
                self._unsaved[record.day] = (
                    self._unsaved.get(record.day, Decimal(0)) + record.cost_usd
                )

    async def summary(self) -> BudgetSummary:
        today = self._today()
        saved = await self._ledger.spent_on(today)
        spent = saved.total_usd + self._unsaved.get(today, Decimal(0))
        return BudgetSummary(
            day=today,
            limit_usd=self._limit,
            spent_usd=spent,
            remaining_usd=max(self._limit - spent, Decimal(0)),
            requests=saved.requests,
        )
