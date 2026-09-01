"""Request-scoped database sessions.

The dependency yields a session, rolls back if the request raises, and always
closes. It deliberately does **not** commit: a caller that changed something
says so explicitly. An auto-committing dependency would bake a persistence
policy into the framework layer before any writer exists, and makes it hard to
tell afterwards which code path actually wrote.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.engine import get_engine


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory."""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        # Attributes stay usable after commit, so a handler can still read the
        # object it just wrote without triggering a surprise lazy load.
        expire_on_commit=False,
    )


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session for one request."""
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
