"""Domain tools end to end, through the executor, against real PostgreSQL."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.rag.embeddings import HashingEmbeddingProvider
from app.services.document import DocumentService
from app.services.ticket import TicketService
from app.tools.execution import ToolExecutor
from app.tools.factory import build_registry
from app.tools.results import ToolOutcome

from .conftest import EXPECTED_DATABASE

pytestmark = pytest.mark.usefixtures("clean_tables")

REFUNDS = "Refunds take 5 business days.\n\nContact billing to start one."


@pytest.fixture
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        assert await opened.scalar(text("SELECT current_database()")) == EXPECTED_DATABASE
        yield opened
    await engine.dispose()


@pytest.fixture
def services(session: AsyncSession) -> tuple[TicketService, DocumentService]:
    return TicketService(session), DocumentService(session, HashingEmbeddingProvider())


@pytest.fixture
def executor(services: tuple[TicketService, DocumentService]) -> ToolExecutor:
    tickets, documents = services
    return ToolExecutor(build_registry(tickets=tickets, documents=documents))


# --------------------------------------------------------------------------
# Registry composition
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_registry_exposes_the_expected_tools(
    services: tuple[TicketService, DocumentService],
) -> None:
    tickets, documents = services
    registry = build_registry(tickets=tickets, documents=documents)

    assert registry.names() == ["list_tickets", "lookup_ticket", "search_knowledge_base"]


@pytest.mark.anyio
async def test_every_tool_is_described_with_a_schema(
    services: tuple[TicketService, DocumentService],
) -> None:
    tickets, documents = services

    for described in build_registry(tickets=tickets, documents=documents).describe_all():
        assert described["description"]
        assert "properties" in described["parameters"]


# --------------------------------------------------------------------------
# lookup_ticket
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_lookup_ticket_returns_plain_data(
    executor: ToolExecutor, services: tuple[TicketService, DocumentService]
) -> None:
    tickets, _ = services
    created = await tickets.create(
        customer_email="ada@example.com", subject="Charged twice", body="Refund please."
    )

    result = await executor.execute("lookup_ticket", {"ticket_id": str(created.id)})

    assert result.ok
    assert result.output["subject"] == "Charged twice"
    assert result.output["status"] == "open"
    assert isinstance(result.output["id"], str)  # serialisable, not an ORM object


@pytest.mark.anyio
async def test_lookup_missing_ticket_is_a_failed_result(executor: ToolExecutor) -> None:
    result = await executor.execute("lookup_ticket", {"ticket_id": str(uuid.uuid4())})

    assert result.outcome is ToolOutcome.FAILED
    assert "No ticket exists" in result.error


@pytest.mark.anyio
async def test_lookup_with_a_malformed_id_is_invalid_params(
    executor: ToolExecutor,
) -> None:
    result = await executor.execute("lookup_ticket", {"ticket_id": "not-a-uuid"})

    assert result.outcome is ToolOutcome.INVALID_PARAMS


# --------------------------------------------------------------------------
# list_tickets
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_tickets_returns_newest_first(
    executor: ToolExecutor, services: tuple[TicketService, DocumentService]
) -> None:
    tickets, _ = services
    for subject in ("one", "two"):
        await tickets.create(customer_email="a@example.com", subject=subject, body="b")

    result = await executor.execute("list_tickets", {"limit": 5})

    assert result.ok
    assert result.output[0]["subject"] == "two"


@pytest.mark.anyio
async def test_list_tickets_filters_by_status(
    executor: ToolExecutor, services: tuple[TicketService, DocumentService]
) -> None:
    tickets, _ = services
    await tickets.create(customer_email="a@example.com", subject="s", body="b")

    assert len((await executor.execute("list_tickets", {"status": "open"})).output) == 1
    assert (await executor.execute("list_tickets", {"status": "closed"})).output == []


@pytest.mark.anyio
async def test_unknown_status_lists_the_valid_ones(executor: ToolExecutor) -> None:
    """The message has to be actionable: a model will read it and retry."""
    result = await executor.execute("list_tickets", {"status": "banana"})

    assert result.outcome is ToolOutcome.FAILED
    assert "open" in result.error and "resolved" in result.error


@pytest.mark.anyio
async def test_list_limit_is_bounded(executor: ToolExecutor) -> None:
    assert (
        await executor.execute("list_tickets", {"limit": 999})
    ).outcome is ToolOutcome.INVALID_PARAMS


# --------------------------------------------------------------------------
# search_knowledge_base
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_returns_passages_with_provenance(
    executor: ToolExecutor, services: tuple[TicketService, DocumentService]
) -> None:
    _, documents = services
    await documents.ingest(title="Refunds", content=REFUNDS)

    result = await executor.execute("search_knowledge_base", {"query": "refund", "top_k": 2})

    assert result.ok
    first = result.output[0]
    assert first["document_title"] == "Refunds"
    assert 0.0 <= first["similarity"] <= 1.0
    assert first["excerpt"]


@pytest.mark.anyio
async def test_search_on_an_empty_corpus_succeeds_with_nothing(
    executor: ToolExecutor,
) -> None:
    """Finding nothing is a valid answer, not a failure."""
    result = await executor.execute("search_knowledge_base", {"query": "anything"})

    assert result.ok
    assert result.output == []


@pytest.mark.anyio
async def test_search_rejects_an_empty_query(executor: ToolExecutor) -> None:
    assert (
        await executor.execute("search_knowledge_base", {"query": ""})
    ).outcome is ToolOutcome.INVALID_PARAMS


# --------------------------------------------------------------------------
# Cross-cutting
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_tool_is_reported_not_raised(executor: ToolExecutor) -> None:
    assert (await executor.execute("delete_everything")).outcome is ToolOutcome.NOT_FOUND


@pytest.mark.anyio
async def test_no_tool_result_leaks_database_internals(
    executor: ToolExecutor, services: tuple[TicketService, DocumentService]
) -> None:
    tickets, _ = services
    await tickets.create(customer_email="a@example.com", subject="s", body="b")

    for name, params in (
        ("lookup_ticket", {"ticket_id": str(uuid.uuid4())}),
        ("list_tickets", {"status": "banana"}),
        ("search_knowledge_base", {"query": "x"}),
    ):
        rendered = str((await executor.execute(name, params)).model_dump())
        for leak in ("postgresql", "asyncpg", "Traceback", "SELECT ", "vector("):
            assert leak not in rendered, name


@pytest.mark.anyio
async def test_tool_output_is_json_serialisable(
    executor: ToolExecutor, services: tuple[TicketService, DocumentService]
) -> None:
    """Results may be serialised into a prompt; ORM objects would not survive."""
    import json

    tickets, documents = services
    created = await tickets.create(customer_email="a@example.com", subject="s", body="b")
    await documents.ingest(title="Refunds", content=REFUNDS)

    for name, params in (
        ("lookup_ticket", {"ticket_id": str(created.id)}),
        ("list_tickets", {}),
        ("search_knowledge_base", {"query": "refund"}),
    ):
        result = await executor.execute(name, params)
        assert result.ok, name
        json.dumps(result.output)
