"""Database configuration and the declarative foundation.

Entirely offline: nothing here opens a connection, so it runs everywhere. The
tests that need a real server live in ``tests/backend/db/``.
"""

import logging

import pytest
from sqlalchemy import DateTime

from app.core.config import Settings
from app.core.logging import REDACTED, SecretRedactingFilter
from app.db.base import NAMING_CONVENTION, Base, TimestampMixin
from app.db.engine import build_engine
from app.db.errors import (
    DatabaseError,
    DatabaseNotConfiguredError,
    DatabaseUnavailableError,
)

ASYNC_URL = "postgresql+asyncpg://localhost:5432/nexaassist"
URL_WITH_PASSWORD = "postgresql+asyncpg://dbuser:s3cr3tpassword@db.internal:5432/nexa"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_database_url_is_unset_by_default() -> None:
    assert Settings().database_url is None


def test_blank_database_url_is_treated_as_unset() -> None:
    """`.env.example` may ship it blank; blank must mean unset, not empty."""
    assert Settings(database_url="").database_url is None


def test_async_driver_url_is_accepted() -> None:
    settings = Settings(database_url=ASYNC_URL)

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == ASYNC_URL


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://localhost:5432/nexaassist",
        "postgresql+psycopg2://localhost:5432/nexaassist",
        "sqlite+aiosqlite:///./nexa.db",
    ],
)
def test_synchronous_or_foreign_drivers_are_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="postgresql\\+asyncpg"):
        Settings(database_url=url)


def test_driver_rejection_never_echoes_the_credentials() -> None:
    """The error quotes the scheme only -- the rest may hold a password."""
    with pytest.raises(ValueError) as excinfo:
        Settings(database_url="postgresql://dbuser:s3cr3tpassword@db.internal/nexa")

    assert "s3cr3tpassword" not in str(excinfo.value)
    assert "dbuser" not in str(excinfo.value)


def test_pool_settings_have_sane_defaults_and_bounds() -> None:
    settings = Settings()

    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 10
    assert settings.db_echo is False
    with pytest.raises(ValueError):
        Settings(db_pool_size=0)


def test_database_url_is_a_secret_and_does_not_leak_through_repr() -> None:
    settings = Settings(database_url=URL_WITH_PASSWORD)

    assert "s3cr3tpassword" not in repr(settings)


# --------------------------------------------------------------------------
# Redaction of URL credentials
# --------------------------------------------------------------------------


def test_url_password_is_redacted_in_logs() -> None:
    result = SecretRedactingFilter().redact(f"connecting to {URL_WITH_PASSWORD}")

    assert "s3cr3tpassword" not in result
    assert REDACTED in result
    assert "db.internal" in result  # host stays useful for debugging


def test_url_password_redacted_through_a_format_argument() -> None:
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="url=%s", args=(URL_WITH_PASSWORD,), exc_info=None,
    )

    SecretRedactingFilter().filter(record)

    assert "s3cr3tpassword" not in record.getMessage()


def test_credential_free_url_is_left_alone() -> None:
    assert SecretRedactingFilter().redact(ASYNC_URL) == ASYNC_URL


# --------------------------------------------------------------------------
# Engine construction (no connection is opened)
# --------------------------------------------------------------------------


def test_engine_is_built_from_settings() -> None:
    engine = build_engine(
        Settings(database_url=ASYNC_URL, db_pool_size=7, db_max_overflow=3)
    )

    assert engine.url.database == "nexaassist"
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.pool.size() == 7


def test_engine_url_masks_the_password_when_rendered() -> None:
    engine = build_engine(Settings(database_url=URL_WITH_PASSWORD))

    assert "s3cr3tpassword" not in engine.url.render_as_string()


def test_building_without_a_url_raises_the_configuration_error() -> None:
    with pytest.raises(DatabaseNotConfiguredError) as excinfo:
        build_engine(Settings(database_url=None))

    assert excinfo.value.status_code == 500
    assert excinfo.value.code == "database_not_configured"


# --------------------------------------------------------------------------
# Error hierarchy
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (DatabaseError(), 503, "database_error"),
        (DatabaseNotConfiguredError(), 500, "database_not_configured"),
        (DatabaseUnavailableError(), 503, "database_unavailable"),
    ],
)
def test_database_errors_carry_their_contract(
    error: DatabaseError, status: int, code: str
) -> None:
    assert isinstance(error, DatabaseError)
    assert error.status_code == status
    assert error.code == code
    assert error.to_response().code == code


def test_database_errors_render_through_the_shared_envelope() -> None:
    from app.core.exceptions import AppError

    assert issubclass(DatabaseError, AppError)


# --------------------------------------------------------------------------
# Declarative foundation
# --------------------------------------------------------------------------


def test_metadata_carries_the_naming_convention() -> None:
    """Settled before the first table exists; see app/db/base.py."""
    assert dict(Base.metadata.naming_convention) == NAMING_CONVENTION


def test_naming_convention_covers_every_constraint_kind() -> None:
    assert set(NAMING_CONVENTION) == {"ix", "uq", "ck", "fk", "pk"}


def test_metadata_holds_exactly_the_registered_domain_tables() -> None:
    """Guards against a table appearing without anyone noticing.

    Was ``metadata == {}`` while M3 shipped no business tables; M4 adds the
    first two, so the guard now pins the expected set instead.
    """
    assert set(Base.metadata.tables) == {
        "customers",
        "document_chunks",
        "documents",
        "tickets",
    }


def test_timestamp_mixin_columns_are_timezone_aware() -> None:
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

    class Local(DeclarativeBase):
        pass

    class Sample(TimestampMixin, Local):
        __tablename__ = "sample_timestamp_probe"
        id: Mapped[int] = mapped_column(primary_key=True)

    for name in ("created_at", "updated_at"):
        column = Sample.__table__.columns[name]
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert column.nullable is False
        assert column.server_default is not None
