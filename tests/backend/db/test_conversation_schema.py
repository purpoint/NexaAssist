"""Conversation schema and integrity, against real PostgreSQL."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models import Conversation, ConversationMessage, MessageRole

from .conftest import EXPECTED_DATABASE

pytestmark = pytest.mark.usefixtures("clean_tables")

CONV = Conversation.__table__
MSG = ConversationMessage.__table__


async def customer(engine: AsyncEngine, email: str = "ada@example.com") -> uuid.UUID:
    async with engine.begin() as c:
        return await c.scalar(
            text("INSERT INTO customers (email) VALUES (:e) RETURNING id"), {"e": email}
        )


async def conversation(engine: AsyncEngine, cust: uuid.UUID) -> uuid.UUID:
    async with engine.begin() as c:
        return await c.scalar(
            text("INSERT INTO conversations (customer_id) VALUES (:c) RETURNING id"),
            {"c": cust},
        )


async def message(engine: AsyncEngine, conv: uuid.UUID, position: int, **kw) -> uuid.UUID:
    payload = {
        "c": conv,
        "p": position,
        "r": kw.get("role", "customer"),
        "t": kw.get("content", "hello"),
        "k": kw.get("tokens", 3),
    }
    async with engine.begin() as c:
        return await c.scalar(
            text(
                "INSERT INTO conversation_messages "
                "(conversation_id, position, role, content, token_estimate) "
                "VALUES (:c,:p,:r,:t,:k) RETURNING id"
            ),
            payload,
        )


# --------------------------------------------------------------------------
# Model shape (offline assertions on metadata)
# --------------------------------------------------------------------------


def test_message_roles() -> None:
    assert [r.value for r in MessageRole] == ["customer", "assistant"]


def test_position_is_explicit_not_inferred_from_time() -> None:
    """Timestamps collide; an order that relies on a tie-break replays wrongly."""
    assert "position" in MSG.columns
    assert any(
        c.name == "uq_conversation_messages_conversation_id_position"
        for c in MSG.constraints
    )


def test_relationships_refuse_to_lazy_load() -> None:
    assert Conversation.__mapper__.relationships["messages"].lazy == "raise"
    assert ConversationMessage.__mapper__.relationships["conversation"].lazy == "raise"


# --------------------------------------------------------------------------
# Live schema
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_targets_only_the_test_database(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT current_database()")) == EXPECTED_DATABASE


@pytest.mark.anyio
async def test_a_conversation_holds_ordered_messages(engine: AsyncEngine) -> None:
    conv = await conversation(engine, await customer(engine))
    await message(engine, conv, 0, role="customer", content="why was I charged")
    await message(engine, conv, 1, role="assistant", content="looking into it")

    async with engine.connect() as c:
        rows = await c.execute(
            text(
                "SELECT position, role FROM conversation_messages "
                "WHERE conversation_id=:c ORDER BY position"
            ),
            {"c": conv},
        )

    assert [(r[0], r[1]) for r in rows] == [(0, "customer"), (1, "assistant")]


@pytest.mark.anyio
async def test_positions_are_unique_within_a_conversation(engine: AsyncEngine) -> None:
    conv = await conversation(engine, await customer(engine))
    await message(engine, conv, 0)

    with pytest.raises(IntegrityError):
        await message(engine, conv, 0)


@pytest.mark.anyio
async def test_the_same_position_is_fine_in_another_conversation(
    engine: AsyncEngine,
) -> None:
    cust = await customer(engine)
    first, second = await conversation(engine, cust), await conversation(engine, cust)
    await message(engine, first, 0)

    assert await message(engine, second, 0)


@pytest.mark.anyio
async def test_deleting_a_conversation_removes_its_messages(engine: AsyncEngine) -> None:
    conv = await conversation(engine, await customer(engine))
    await message(engine, conv, 0)

    async with engine.begin() as c:
        await c.execute(text("DELETE FROM conversations WHERE id=:i"), {"i": conv})
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM conversation_messages")) == 0


@pytest.mark.anyio
async def test_a_customer_with_conversations_cannot_be_deleted(
    engine: AsyncEngine,
) -> None:
    """History must not vanish with the customer row."""
    cust = await customer(engine)
    await conversation(engine, cust)

    with pytest.raises(IntegrityError):
        async with engine.begin() as c:
            await c.execute(text("DELETE FROM customers WHERE id=:i"), {"i": cust})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [("content", "   "), ("role", "narrator"), ("position", -1), ("tokens", -5)],
)
async def test_invalid_messages_are_rejected(
    engine: AsyncEngine, field: str, value: object
) -> None:
    conv = await conversation(engine, await customer(engine))
    kwargs = {"role": "customer", "content": "hi", "tokens": 1}
    position = 0
    if field == "position":
        position = value
    else:
        kwargs[field] = value

    with pytest.raises(IntegrityError):
        await message(engine, conv, position, **kwargs)


@pytest.mark.anyio
async def test_token_estimate_defaults_to_zero(engine: AsyncEngine) -> None:
    conv = await conversation(engine, await customer(engine))

    async with engine.begin() as c:
        stored = await c.scalar(
            text(
                "INSERT INTO conversation_messages (conversation_id, position, role, content) "
                "VALUES (:c,0,'customer','hi') RETURNING token_estimate"
            ),
            {"c": conv},
        )

    assert stored == 0
