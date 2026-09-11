"""Migrations and models must describe the same schema.

Production schemas change only through Alembic, but local development creates
SQLite tables straight from the models. Two sources of truth for one schema
drift apart quietly: a column added to a model without a migration works on
every laptop and fails the first request after deploying. These tests are what
keeps the two identical.

SQLite always runs. Postgres runs when FORGE_TEST_POSTGRES_URL points at a
disposable database -- the tests downgrade it to empty, so never point it at
one whose data you want.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import normalise_database_url
from app.db.models import Base

BACKEND = pathlib.Path(__file__).resolve().parents[1]
POSTGRES = os.environ.get("FORGE_TEST_POSTGRES_URL")


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = url
    return config


def _inspect(url: str, fn):  # noqa: ANN001, ANN202
    async def run():  # noqa: ANN202
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(fn)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def schema_diff(url: str) -> list:
    return _inspect(
        url,
        lambda sync: compare_metadata(
            MigrationContext.configure(sync, opts={"compare_type": True}),
            Base.metadata,
        ),
    )


def table_names(url: str) -> set[str]:
    return set(_inspect(url, lambda sync: inspect(sync).get_table_names()))


@pytest.fixture(params=["sqlite", "postgres"])
def database_url(request: pytest.FixtureRequest, tmp_path: pathlib.Path) -> Iterator[str]:
    if request.param == "sqlite":
        yield f"sqlite+aiosqlite:///{(tmp_path / 'migrations.db').as_posix()}"
        return

    if not POSTGRES:
        pytest.skip("set FORGE_TEST_POSTGRES_URL to a disposable database to run")
    url = normalise_database_url(POSTGRES)
    command.downgrade(alembic_config(url), "base")
    yield url
    command.downgrade(alembic_config(url), "base")


def test_history_has_exactly_one_head() -> None:
    heads = ScriptDirectory.from_config(alembic_config("sqlite://")).get_heads()
    assert len(heads) == 1, f"migration history has diverged: {heads}"


def test_migrations_build_exactly_the_schema_the_models_describe(
    database_url: str,
) -> None:
    command.upgrade(alembic_config(database_url), "head")
    assert schema_diff(database_url) == []


def test_downgrade_to_base_removes_every_table(database_url: str) -> None:
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert table_names(database_url) <= {"alembic_version"}
