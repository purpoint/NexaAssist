"""The domain models, asserted from metadata alone.

Entirely offline: no connection is opened. The live-schema and integrity tests
live in ``tests/backend/db/test_domain_schema.py``.
"""

import uuid

import pytest
from sqlalchemy import CheckConstraint, DateTime, String, Text, Uuid

from app.db.base import Base
from app.models import Customer, Ticket, TicketStatus

CUSTOMERS = Customer.__table__
TICKETS = Ticket.__table__


def constraint_names(table: object, kind: type) -> set[str]:
    return {c.name for c in table.constraints if isinstance(c, kind)}


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_both_models_are_registered_on_the_shared_metadata() -> None:
    """Autogenerate compares against this; an unimported model looks droppable."""
    assert {"customers", "tickets"} <= set(Base.metadata.tables)


def test_table_names() -> None:
    assert CUSTOMERS.name == "customers"
    assert TICKETS.name == "tickets"


# --------------------------------------------------------------------------
# Columns
# --------------------------------------------------------------------------


@pytest.mark.parametrize("table", [CUSTOMERS, TICKETS], ids=["customers", "tickets"])
def test_primary_key_is_a_server_generated_uuid(table: object) -> None:
    """UUIDs because ids travel in URLs; sequential ids leak volume."""
    column = table.columns["id"]

    assert isinstance(column.type, Uuid)
    assert column.primary_key is True
    assert "gen_random_uuid()" in str(column.server_default.arg)


@pytest.mark.parametrize("table", [CUSTOMERS, TICKETS], ids=["customers", "tickets"])
def test_timestamps_are_timezone_aware_and_server_defaulted(table: object) -> None:
    for name in ("created_at", "updated_at"):
        column = table.columns[name]
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert column.nullable is False
        assert column.server_default is not None


def test_customer_email_column() -> None:
    column = CUSTOMERS.columns["email"]

    assert isinstance(column.type, String)
    assert column.type.length == 320  # RFC maximum
    assert column.nullable is False
    assert column.unique is True


def test_customer_carries_no_extra_fields() -> None:
    """The entity earns its table through identity, not through field count."""
    assert set(CUSTOMERS.columns.keys()) == {"id", "email", "created_at", "updated_at"}


def test_ticket_columns() -> None:
    assert set(TICKETS.columns.keys()) == {
        "id",
        "customer_id",
        "subject",
        "body",
        "status",
        # M19 added ownership. Nullable on purpose: a row created by a
        # deployment that does not scope by subject has no owner.
        "owner_subject",
        "created_at",
        "updated_at",
    }
    assert TICKETS.columns["owner_subject"].nullable is True
    assert isinstance(TICKETS.columns["subject"].type, String)
    assert TICKETS.columns["subject"].type.length == 200
    assert isinstance(TICKETS.columns["body"].type, Text)
    assert TICKETS.columns["customer_id"].nullable is False


def test_ticket_status_defaults_to_open_server_side() -> None:
    assert TICKETS.columns["status"].server_default.arg == "open"


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


def test_ticket_status_values() -> None:
    assert [s.value for s in TicketStatus] == ["open", "pending", "resolved", "closed"]


def test_status_is_varchar_not_a_native_enum() -> None:
    """Native enums need ALTER TYPE to extend and barely shrink at all."""
    column_type = TICKETS.columns["status"].type

    assert getattr(column_type, "native_enum", None) is False
    assert getattr(column_type, "create_constraint", None) is True


# --------------------------------------------------------------------------
# Constraints and indexes, by convention name
# --------------------------------------------------------------------------


def test_check_constraint_names_follow_the_convention() -> None:
    assert constraint_names(CUSTOMERS, CheckConstraint) >= {
        "ck_customers_email_lowercase",
        "ck_customers_email_not_blank",
    }
    assert constraint_names(TICKETS, CheckConstraint) >= {
        "ck_tickets_subject_not_blank",
        "ck_tickets_body_not_blank",
        "ck_tickets_status_valid",
    }


def test_unique_and_primary_key_names() -> None:
    assert CUSTOMERS.primary_key.name == "pk_customers"
    assert TICKETS.primary_key.name == "pk_tickets"
    assert any(c.name == "uq_customers_email" for c in CUSTOMERS.constraints)


def test_foreign_key_name_restricts_deletes() -> None:
    """RESTRICT so removing a customer cannot destroy their ticket history."""
    fk = next(iter(TICKETS.columns["customer_id"].foreign_keys))

    assert isinstance(TICKETS.columns["customer_id"].foreign_keys, set)
    assert fk.constraint.name == "fk_tickets_customer_id_customers"
    assert fk.ondelete == "RESTRICT"
    assert fk.column is CUSTOMERS.columns["id"]


def test_indexes_match_the_queries_they_serve() -> None:
    assert {i.name for i in TICKETS.indexes} == {
        "ix_tickets_customer_id",
        "ix_tickets_created_at",
        "ix_tickets_status_created_at",
        # M19: every scoped listing filters on the owner.
        "ix_tickets_owner_subject",
    }


def test_composite_index_column_order() -> None:
    """(status, created_at): filter first, then the ordering column."""
    composite = next(
        i for i in TICKETS.indexes if i.name == "ix_tickets_status_created_at"
    )

    assert [c.name for c in composite.columns] == ["status", "created_at"]


# --------------------------------------------------------------------------
# Relationships
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "attribute"), [(Customer, "tickets"), (Ticket, "customer")]
)
def test_relationships_refuse_to_lazy_load(model: type, attribute: str) -> None:
    """Under asyncio a lazy load fails far from its cause, or silently works
    in a sync test and breaks in production. lazy="raise" makes it loud."""
    assert model.__mapper__.relationships[attribute].lazy == "raise"


def test_relationships_are_bidirectional() -> None:
    assert Customer.__mapper__.relationships["tickets"].back_populates == "customer"
    assert Ticket.__mapper__.relationships["customer"].back_populates == "tickets"


def test_instances_can_be_constructed_without_a_database() -> None:
    ticket = Ticket(
        customer_id=uuid.uuid4(), subject="Charged twice", body="Please refund."
    )

    assert ticket.subject == "Charged twice"
    assert ticket.id is None  # assigned by the database, not by Python
