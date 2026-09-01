"""Alembic environment.

The database URL comes from the application's ``Settings`` -- the same object
the running service uses -- so there is exactly one place that decides which
database is addressed. ``alembic.ini`` deliberately carries no
``sqlalchemy.url``: a URL in a tracked file is a credential waiting to be
committed.

A caller may still override the URL programmatically (the test suite points at
``nexaassist_test`` this way). That is an explicit, documented override rather
than a second source of configuration.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings

# Importing app.models populates Base.metadata with every mapped table, which
# is what autogenerate compares the live database against. Without it,
# autogenerate would cheerfully propose dropping tables it cannot see.
from app.models import metadata as target_metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def database_url() -> str:
    """Resolve the URL: explicit override first, otherwise application settings."""
    override = config.get_main_option("sqlalchemy.url", None)
    if override:
        return override

    settings = get_settings()
    if settings.database_url is None:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set it in the environment (or "
            "in .env) before running migrations."
        )
    return settings.database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (``alembic ... --sql``)."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Constraint names are rendered explicitly, so a generated DROP always
        # names something the database actually knows.
        render_as_batch=False,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detects column type changes, which Alembic otherwise ignores and
        # which then diverge silently between environments.
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        # No pooling: a migration run is a short-lived process.
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
