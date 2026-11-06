"""Alembic environment. The `llm` schema and role are created by deploy/postgres/init."""

import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from llm_gateway.ledger import metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, version_table_schema="llm")
    with context.begin_transaction():
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(context.config.attributes["url"])
    async with engine.connect() as connection:
        await connection.run_sync(_run)
        await connection.commit()
    await engine.dispose()


asyncio.run(_main())
