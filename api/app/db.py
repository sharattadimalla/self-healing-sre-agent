"""Async SQLAlchemy engine/session holder.

The engine is wrapped so ``reset_pool`` remediation can dispose and rebuild it
without restarting the process. SQLite (tests) gets a shared static pool; the
pool-size knob only applies to real drivers.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def _make_engine(url: str, pool_size: int, pool_timeout: float = 3.0) -> AsyncEngine:
    if url.startswith("sqlite"):
        return create_async_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_async_engine(
        url,
        future=True,
        pool_size=pool_size,
        max_overflow=pool_size,
        pool_pre_ping=True,
        pool_timeout=pool_timeout,
    )


class Database:
    def __init__(self, url: str, pool_size: int, pool_timeout: float = 3.0) -> None:
        self._url = url
        self._pool_size = pool_size
        self._pool_timeout = pool_timeout
        self._lock = asyncio.Lock()
        self.engine: AsyncEngine = _make_engine(url, pool_size, pool_timeout)
        self.sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def reset(self) -> None:
        """Build a fresh engine, then dispose the old one (EC-04)."""
        async with self._lock:
            old = self.engine
            new = _make_engine(self._url, self._pool_size, self._pool_timeout)
            self.engine = new
            self.sessionmaker = async_sessionmaker(new, expire_on_commit=False)
            await old.dispose()

    async def dispose(self) -> None:
        await self.engine.dispose()
