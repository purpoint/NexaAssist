"""Connection pool construction and lifecycle.

The engine is created once per process and disposed at shutdown. Creating it
does **not** open a connection -- SQLAlchemy pools lazily -- so a misconfigured
or unreachable database surfaces at first use or at the readiness check, not as
a startup crash. That keeps the service runnable for work that needs no
database, the same way an absent provider key does not stop the app.

No schema is ever created from here. ``Base.metadata.create_all`` is not called
anywhere in application code: the schema belongs to migrations, and having two
mechanisms that both claim to own it is how environments drift apart.
"""

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.errors import DatabaseNotConfiguredError

logger = get_logger(__name__)


def build_engine(settings: Settings) -> AsyncEngine:
    """Construct an engine from settings.

    Raises :class:`DatabaseNotConfiguredError` when no URL is set, rather than
    fabricating a default that would silently point somewhere unintended.
    """
    if settings.database_url is None:
        raise DatabaseNotConfiguredError()

    return create_async_engine(
        settings.database_url.get_secret_value(),
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Cheap liveness probe per checkout. Without it, a connection killed by
        # a restart or an idle timeout is handed to a caller and fails there.
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the process-wide engine, building it on first use."""
    engine = build_engine(get_settings())
    # url.render_as_string() masks the password by default; never pass
    # hide_password=False.
    logger.info("database engine ready url=%s", engine.url.render_as_string())
    return engine


async def dispose_engine() -> None:
    """Close every pooled connection. Safe when no engine was ever built."""
    if get_engine.cache_info().currsize == 0:
        return
    await get_engine().dispose()
    get_engine.cache_clear()
    logger.info("database engine disposed")
