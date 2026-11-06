"""The same ledger suite against real Postgres, plus durability and migration checks."""

import os
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from llm_gateway.ledger import LedgerError, PostgresLedger
from llm_gateway.migrate import upgrade_to_head

from ..ledger_contract import DAY, LedgerContract, record

pytestmark = pytest.mark.integration


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """Uses SCOUT_TEST_DATABASE_URL when set, otherwise starts a container."""
    configured = os.environ.get("SCOUT_TEST_DATABASE_URL")
    if configured:
        yield configured
        return

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture
async def clean_url(database_url: str) -> AsyncIterator[str]:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS llm CASCADE"))
        await connection.execute(text("CREATE SCHEMA llm"))
    await engine.dispose()
    upgrade_to_head(database_url)
    yield database_url


class TestPostgresLedger(LedgerContract):
    @pytest.fixture
    async def ledger(self, clean_url: str) -> AsyncIterator[PostgresLedger]:
        store = PostgresLedger(clean_url)
        yield store
        await store.close()


@pytest.mark.anyio
async def test_spend_survives_a_new_connection(clean_url: str) -> None:
    first = PostgresLedger(clean_url)
    await first.record(record("0.5"))
    await first.close()

    second = PostgresLedger(clean_url)
    spent = await second.spent_on(DAY)
    await second.close()

    assert spent.total_usd == Decimal("0.5")


@pytest.mark.anyio
async def test_migrations_can_run_twice(clean_url: str) -> None:
    upgrade_to_head(clean_url)


@pytest.mark.anyio
async def test_token_details_are_stored(clean_url: str) -> None:
    store = PostgresLedger(clean_url)
    await store.record(record("0.5"))
    await store.close()
    engine = create_async_engine(clean_url)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT input_tokens, cache_read_tokens, estimated FROM llm.usage_records")
            )
        ).one()
    await engine.dispose()

    assert tuple(row) == (10, 7, False)


@pytest.mark.anyio
async def test_database_outage_becomes_a_ledger_error() -> None:
    store = PostgresLedger("postgresql+asyncpg://nobody:wrong@127.0.0.1:9/nothing")

    with pytest.raises(LedgerError):
        await store.spent_on(DAY)

    await store.close()
