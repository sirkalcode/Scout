"""Where spend is stored. Postgres in real use, memory in fast tests."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    MetaData,
    Numeric,
    String,
    Table,
    func,
    insert,
    select,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from .usage import Usage

logger = logging.getLogger(__name__)

metadata = MetaData(schema="llm")
usage_records = Table(
    "usage_records",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("day", Date, nullable=False, index=True),
    Column("request_id", String(200), nullable=False),
    Column("model", String(100), nullable=False),
    Column("input_tokens", BigInteger, nullable=False),
    Column("output_tokens", BigInteger, nullable=False),
    Column("cache_read_tokens", BigInteger, nullable=False),
    Column("cache_write_5m_tokens", BigInteger, nullable=False),
    Column("cache_write_1h_tokens", BigInteger, nullable=False),
    Column("web_search_requests", BigInteger, nullable=False),
    Column("cost_usd", Numeric(20, 10), nullable=False),
    Column("estimated", Boolean, nullable=False),
)


class LedgerError(Exception):
    """Spend could not be read or written."""


@dataclass(frozen=True)
class SpendRecord:
    created_at: datetime
    request_id: str
    model: str
    usage: Usage
    cost_usd: Decimal
    estimated: bool

    @property
    def day(self) -> date:
        return self.created_at.astimezone(UTC).date()


@dataclass(frozen=True)
class DaySpend:
    total_usd: Decimal
    requests: int


class SpendLedger(Protocol):
    async def record(self, record: SpendRecord) -> None: ...

    async def spent_on(self, day: date) -> DaySpend: ...

    async def close(self) -> None: ...


@dataclass
class InMemoryLedger:
    records: list[SpendRecord] = field(default_factory=list[SpendRecord])

    async def record(self, record: SpendRecord) -> None:
        self.records.append(record)

    async def spent_on(self, day: date) -> DaySpend:
        matching = [record.cost_usd for record in self.records if record.day == day]
        return DaySpend(sum(matching, Decimal(0)), len(matching))

    async def close(self) -> None:
        return None


class PostgresLedger:
    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(
            url, pool_pre_ping=True, connect_args={"timeout": 5, "command_timeout": 5}
        )

    async def record(self, record: SpendRecord) -> None:
        statement = insert(usage_records).values(
            created_at=record.created_at,
            day=record.day,
            request_id=record.request_id,
            model=record.model,
            input_tokens=record.usage.input_tokens,
            output_tokens=record.usage.output_tokens,
            cache_read_tokens=record.usage.cache_read_input_tokens,
            cache_write_5m_tokens=record.usage.cache_write_5m_tokens,
            cache_write_1h_tokens=record.usage.cache_write_1h_tokens,
            web_search_requests=record.usage.web_search_requests,
            cost_usd=record.cost_usd,
            estimated=record.estimated,
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise LedgerError(type(error).__name__) from error

    async def spent_on(self, day: date) -> DaySpend:
        statement = select(
            func.coalesce(func.sum(usage_records.c.cost_usd), 0), func.count()
        ).where(usage_records.c.day == day)
        try:
            async with self._engine.connect() as connection:
                total, requests = (await connection.execute(statement)).one()
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise LedgerError(type(error).__name__) from error
        return DaySpend(Decimal(total), int(requests))

    async def close(self) -> None:
        await self._engine.dispose()
