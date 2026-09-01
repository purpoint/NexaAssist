"""The domain schema as PostgreSQL actually built it, plus its integrity rules.

Every constraint is asserted by trying to violate it. A constraint nobody has
attempted to break is a constraint nobody knows is enforced.
"""

import asyncio
import uuid
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import DOMAIN_TABLES, EXPECTED_DATABASE, alembic_config

pytestmark = pytest.mark.usefixtures("clean_tables")


async def insert_customer(engine: AsyncEngine, email: str = "a@example.com") -> uuid.UUID:
    async with engine.begin() as connection:
        return await connection.scalar(
            text("INSERT INTO customers (email) VALUES (:e) RETURNING id"), {"e": email}
        )


async def insert_ticket(engine: AsyncEngine, **values: Any) -> uuid.UUID:
    payload = {"subject": "Subject", "body": "Body", **values}
    columns = ", ".join(payload)
    params = ", ".join(f":{k}" for k in payload)
    async with engine.begin() as connection:
        return await connection.scalar(
            text(f"INSERT INTO tickets ({columns}) VALUES ({params}) RETURNING id"),
            payload,
        )


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_schema_work_targets_only_the_test_database(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT current_database()")) == (
            EXPECTED_DATABASE
        )


def test_truncate_targets_are_literal_and_child_first() -> None:
    assert DOMAIN_TABLES == (
        "review_items",
        "document_chunks",
        "documents",
        "tickets",
        "customers",
    )


# --------------------------------------------------------------------------
# Live schema
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_both_tables_exist_with_expected_columns(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        for table, expected in (
            ("customers", {"id", "email", "created_at", "updated_at"}),
            (
                "tickets",
                {
                    "id",
                    "customer_id",
                    "subject",
                    "body",
                    "status",
                    "created_at",
                    "updated_at",
                },
            ),
        ):
            rows = await connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:t"
                ),
                {"t": table},
            )
            assert {r[0] for r in rows} == expected, table


@pytest.mark.anyio
async def test_constraints_are_named_by_the_project_convention(
    engine: AsyncEngine,
) -> None:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE connamespace='public'::regnamespace"
            )
        )
    names = {r[0] for r in rows}

    assert {
        "pk_customers",
        "uq_customers_email",
        "ck_customers_email_lowercase",
        "ck_customers_email_not_blank",
        "pk_tickets",
        "fk_tickets_customer_id_customers",
        "ck_tickets_subject_not_blank",
        "ck_tickets_body_not_blank",
        "ck_tickets_status_valid",
    } <= names


@pytest.mark.anyio
async def test_indexes_exist(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename='tickets'")
        )

    assert {
        "ix_tickets_customer_id",
        "ix_tickets_created_at",
        "ix_tickets_status_created_at",
    } <= {r[0] for r in rows}


@pytest.mark.anyio
async def test_foreign_key_restricts_on_delete(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        # Cast in SQL: confdeltype is PostgreSQL's internal "char" type, which
        # the driver does not hand back as a plain string.
        rule = await connection.scalar(
            text(
                "SELECT confdeltype::text FROM pg_constraint "
                "WHERE conname='fk_tickets_customer_id_customers'"
            )
        )

    assert rule == "r"  # 'r' = RESTRICT


# --------------------------------------------------------------------------
# Server-side defaults
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_defaults_are_applied_by_the_database(engine: AsyncEngine) -> None:
    customer_id = await insert_customer(engine)
    ticket_id = await insert_ticket(engine, customer_id=customer_id)

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT id, status, created_at, updated_at FROM tickets "
                    "WHERE id=:i"
                ),
                {"i": ticket_id},
            )
        ).one()

    assert isinstance(row.id, uuid.UUID)
    assert row.status == "open"
    assert row.created_at is not None and row.updated_at is not None


# --------------------------------------------------------------------------
# Integrity: every constraint, violated on purpose
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ticket_requires_an_existing_customer(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        await insert_ticket(engine, customer_id=uuid.uuid4())


@pytest.mark.anyio
async def test_customer_with_tickets_cannot_be_deleted(engine: AsyncEngine) -> None:
    """RESTRICT: support history must not vanish with the customer row."""
    customer_id = await insert_customer(engine)
    await insert_ticket(engine, customer_id=customer_id)

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM customers WHERE id=:i"), {"i": customer_id}
            )


@pytest.mark.anyio
async def test_email_is_unique(engine: AsyncEngine) -> None:
    await insert_customer(engine, "dup@example.com")

    with pytest.raises(IntegrityError):
        await insert_customer(engine, "dup@example.com")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "email", ["Mixed@Example.com", "   ", ""], ids=["uppercase", "blank", "empty"]
)
async def test_invalid_emails_are_rejected(engine: AsyncEngine, email: str) -> None:
    with pytest.raises(IntegrityError):
        await insert_customer(engine, email)


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["subject", "body"])
async def test_blank_ticket_text_is_rejected(engine: AsyncEngine, field: str) -> None:
    customer_id = await insert_customer(engine)

    with pytest.raises(IntegrityError):
        await insert_ticket(engine, customer_id=customer_id, **{field: "   "})


@pytest.mark.anyio
async def test_unknown_status_is_rejected(engine: AsyncEngine) -> None:
    customer_id = await insert_customer(engine)

    with pytest.raises(IntegrityError):
        await insert_ticket(engine, customer_id=customer_id, status="banana")


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["open", "pending", "resolved", "closed"])
async def test_every_declared_status_is_accepted(
    engine: AsyncEngine, status: str
) -> None:
    customer_id = await insert_customer(engine)

    assert await insert_ticket(engine, customer_id=customer_id, status=status)


# --------------------------------------------------------------------------
# Migration round trip
# --------------------------------------------------------------------------


def test_domain_migration_round_trips(test_database_url: str) -> None:
    """Rollback has to work, not merely be declared."""
    config: Config = alembic_config(test_database_url)

    command.downgrade(config, "base")
    # alembic_version survives a downgrade to base -- Alembic keeps its own
    # bookkeeping table; only the domain tables go.
    assert asyncio.run(_public_tables(test_database_url)) == ["alembic_version"]

    command.upgrade(config, "head")
    assert asyncio.run(_public_tables(test_database_url)) == [
        "alembic_version",
        "customers",
        "document_chunks",
        "documents",
        "review_items",
        "tickets",
    ]


async def _public_tables(url: str) -> list[str]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    created = create_async_engine(url, poolclass=NullPool)
    try:
        async with created.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name"
                )
            )
            return [r[0] for r in rows]
    finally:
        await created.dispose()
