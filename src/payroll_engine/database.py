"""Database connection and session management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from payroll_engine.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def get_engine() -> AsyncEngine:
    """Create async database engine."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


# Global engine and session factory
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Initialize database engine and session factory."""
    global _engine, _session_factory
    if _engine is None:
        _engine = get_engine()
        _session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _engine, _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session."""
    _, factory = init_db()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def acquire_advisory_lock(session: AsyncSession, pay_run_id: str) -> bool:
    """Acquire advisory lock for a pay run (used during commit).

    Returns True if lock acquired, False if already held.
    """
    result = await session.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:pay_run_id))"),
        {"pay_run_id": pay_run_id},
    )
    row = result.scalar()
    return bool(row)


async def release_advisory_lock(session: AsyncSession, pay_run_id: str) -> None:
    """Release advisory lock for a pay run."""
    await session.execute(
        text("SELECT pg_advisory_unlock(hashtext(:pay_run_id))"),
        {"pay_run_id": pay_run_id},
    )
