"""Alembic environment.

Two decisions here are load-bearing:

* The URL comes from ``app.config``, not ``alembic.ini``. Migrations and the
  running app then read the same setting -- including the normalisation that
  turns a pasted Neon or Render connection string into one asyncpg accepts --
  and a password containing ``%`` never meets ConfigParser interpolation.
* SQLite runs in batch mode. It cannot ALTER most columns in place, so a later
  migration that changes one would otherwise pass on Postgres and fail on a
  developer's machine.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    # Leave existing loggers alone, so running migrations from inside the app
    # or a test does not silence everything configured before it.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    # Tests pass a throwaway database through config.attributes rather than
    # the environment, so they cannot migrate the wrong database by accident.
    return config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL instead of connecting (``alembic upgrade head --sql``)."""
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # NullPool: a migration is one short conversation, and a pooled connection
    # left behind would keep a Neon compute awake for nothing.
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
