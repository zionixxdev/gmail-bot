"""
db/session.py — Async SQLAlchemy engine and session factory.

Usage:
    async with get_session() as session:
        result = await session.execute(select(User))
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import DATABASE_URL
from db.base import Base

# ─── Engine ──────────────────────────────────────────────────────────────────

_engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    # For SQLite: connect_args to avoid "same thread" errors
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,
)

# ─── Session factory ─────────────────────────────────────────────────────────

_async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async session with automatic commit/rollback."""
    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables. Call once at startup."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Dispose engine connection pool (call on shutdown)."""
    await _engine.dispose()
