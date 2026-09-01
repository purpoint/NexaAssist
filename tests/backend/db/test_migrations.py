"""Migrations run against a real PostgreSQL instance.

Every statement targets ``nexaassist_test``. These tests are synchronous
because Alembic's async ``env.py`` calls ``asyncio.run`` itself, which cannot
be done from inside a running event loop; where a query is needed, it is driven
through ``asyncio.run`` explicitly.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Column, Integer, MetaData, String, Table, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import NAMING_CONVENTION

from .conftest import EXPECTED_DATABASE

BACKEND = Path(__file__).resolve().parents[3] / "backend"


def alembic_config(url: str) -> Config:
    """Alembic config pointed explicitly at the test database.

    Uses the documented programmatic override rather than writing a URL into
    the tracked ini.
    """
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _scalar(url: str, statement: str) -> Any:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(statement))
    finally:
        await engine.dispose()


def scalar(url: str, statement: str) -> Any:
    return asyncio.run(_scalar(url, statement))


def head_revision() -> str:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


@pytest.fixture
def migrated(test_database_url: str) -> Any:
    """Leave the test database at head, whatever the test did to it."""
    config = alembic_config(test_database_url)
    command.upgrade(config, "head")
    yield config
    command.upgrade(config, "head")


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


def test_migrations_target_only_the_test_database(test_database_url: str) -> None:
    assert EXPECTED_DATABASE in test_database_url
    assert scalar(test_database_url, "SELECT current_database()") == EXPECTED_DATABASE


# --------------------------------------------------------------------------
# Upgrade / downgrade round trip
# --------------------------------------------------------------------------


def test_upgrade_head_stamps_the_expected_revision(
    migrated: Config, test_database_url: str
) -> None:
    stamped = scalar(test_database_url, "SELECT version_num FROM alembic_version")

    assert stamped == head_revision()


def test_downgrade_base_then_upgrade_head_round_trips(
    migrated: Config, test_database_url: str
) -> None:
    """Rollback has to actually work, not merely be declared."""
    command.downgrade(migrated, "base")
    remaining = scalar(
        test_database_url,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='alembic_version'",
    )
    versions_after_downgrade = (
        scalar(test_database_url, "SELECT count(*) FROM alembic_version")
        if remaining
        else 0
    )

    command.upgrade(migrated, "head")
    stamped = scalar(test_database_url, "SELECT version_num FROM alembic_version")

    assert versions_after_downgrade == 0
    assert stamped == head_revision()


def test_upgrade_is_idempotent(migrated: Config, test_database_url: str) -> None:
    command.upgrade(migrated, "head")

    assert scalar(test_database_url, "SELECT count(*) FROM alembic_version") == 1


def test_head_creates_exactly_the_expected_tables(
    migrated: Config, test_database_url: str
) -> None:
    """At head the schema is bookkeeping plus the M4 domain tables.

    Was "no business tables" while the baseline stood alone. Pinning the exact
    set keeps the original value of the check -- an unexpected table still
    fails it.
    """
    tables = scalar(
        test_database_url,
        "SELECT string_agg(table_name, ',' ORDER BY table_name) "
        "FROM information_schema.tables WHERE table_schema='public'",
    )

    assert tables == "alembic_version,customers,tickets"


# --------------------------------------------------------------------------
# Autogenerate
# --------------------------------------------------------------------------


async def _diffs(url: str, metadata: MetaData) -> list[Any]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: compare_metadata(
                    MigrationContext.configure(sync_connection), metadata
                )
            )
    finally:
        await engine.dispose()


def test_autogenerate_detects_a_new_table(
    migrated: Config, test_database_url: str
) -> None:
    """Proves the autogenerate wiring works without shipping a domain table.

    The probe model lives on its own MetaData, so it never touches
    ``Base.metadata`` and cannot leak into a real migration.
    """
    probe = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "autogenerate_probe",
        probe,
        Column("id", Integer, primary_key=True),
        Column("label", String(50), nullable=False),
    )

    diffs = asyncio.run(_diffs(test_database_url, probe))

    added = [d for d in diffs if isinstance(d, tuple) and d[0] == "add_table"]
    assert added, f"autogenerate saw no new table: {diffs}"
    assert added[0][1].name == "autogenerate_probe"


def test_autogenerate_reports_nothing_for_current_metadata(
    migrated: Config, test_database_url: str
) -> None:
    """At head, the shipped metadata and the database agree."""
    from app.models import metadata

    diffs = asyncio.run(_diffs(test_database_url, metadata))
    added = [d for d in diffs if isinstance(d, tuple) and d[0] == "add_table"]

    assert added == []
