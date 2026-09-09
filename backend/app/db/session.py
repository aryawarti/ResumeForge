"""Async engine, session factory, and the soft-delete query helper.

The engine is created lazily so importing the app never opens a connection --
which matters for tests and for the CLI, neither of which needs a database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import get_settings
from .models import Base

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

T = TypeVar("T")


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            # Render free instances are small; a large pool starves the box
            # rather than helping it.
            pool_size=5,
            max_overflow=5,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session per request."""
    async with get_sessionmaker()() as session:
        yield session


async def create_all() -> None:
    """Create tables directly. Alembic owns this in production."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def live(model: type[T]) -> Select[tuple[T]]:
    """Select only rows that have not been soft-deleted.

    Every read path is expected to start here rather than from a bare
    ``select()``, so a forgotten filter cannot leak deleted rows.
    """
    return select(model).where(model.deleted_at.is_(None))  # type: ignore[attr-defined]
