"""The workflow library run against real tickets and a real knowledge base."""

import logging
import re
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.rag.embeddings import HashingEmbeddingProvider
from app.services.document import DocumentService
from app.services.ticket import TicketService
from app.tools.results import ToolOutcome
from app.workflows import library
from app.workflows.errors import WorkflowNotFoundError
from app.workflows.execution import unresolved_references
from app.workflows.factory import build_runner

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
def runner(session: AsyncSession):
    return build_runner(session=session, embedder=HashingEmbeddingProvider())


# --------------------------------------------------------------------------
# The library
# --------------------------------------------------------------------------


def test_library_names_are_stable() -> None:
    assert library.names() == [
        "knowledge_lookup",
        "open_ticket_review",
        "ticket_context",
    ]


def test_every_workflow_is_described_with_its_inputs_and_tools() -> None:
    for described in library.describe_all():
        assert described["description"]
        assert described["steps"]
        assert described["tools"]


def test_get_returns_the_named_workflow() -> None:
    assert library.get("ticket_context").name == "ticket_context"


def test_an_unknown_workflow_is_a_404_error() -> None:
    with pytest.raises(WorkflowNotFoundError) as excinfo:
        library.get("nope")

    assert excinfo.value.status_code == 404
    assert excinfo.value.details == {"workflow": "nope"}


@pytest.mark.anyio
async def test_every_workflow_only_uses_tools_that_exist(session: AsyncSession) -> None:
    """A workflow naming a tool the registry lacks would fail only at run time."""
    from app.services.document import DocumentService as DS
    from app.tools.factory import build_registry

    available = set(
        build_registry(
            tickets=TicketService(session),
            documents=DS(session, HashingEmbeddingProvider()),
        ).names()
    )

    for workflow in library.WORKFLOWS:
        assert set(workflow.tools_used()) <= available, workflow.name


def test_declared_inputs_cover_every_reference() -> None:
    for workflow in library.WORKFLOWS:
        assert unresolved_references(workflow, set(workflow.inputs)) == set()


# --------------------------------------------------------------------------
# Running them for real
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ticket_context_gathers_a_real_ticket(
    runner, session: AsyncSession
) -> None:
    ticket = await TicketService(session).create(
        customer_email="ada@example.com", subject="Charged twice", body="Refund please."
    )

    run, outputs = await runner.run(
        library.get("ticket_context"), inputs={"ticket_id": str(ticket.id)}
    )

    assert run.completed is True
    assert [s.id for s in run.steps] == ["ticket", "recent"]
    assert outputs["ticket"]["subject"] == "Charged twice"
    assert len(outputs["recent"]) == 1


@pytest.mark.anyio
async def test_knowledge_lookup_finds_a_real_passage(
    runner, session: AsyncSession
) -> None:
    await DocumentService(session, HashingEmbeddingProvider()).ingest(
        title="Refunds", content=REFUNDS
    )

    run, outputs = await runner.run(
        library.get("knowledge_lookup"), inputs={"question": "refund"}
    )

    assert run.completed is True
    assert outputs["passages"][0]["document_title"] == "Refunds"


@pytest.mark.anyio
async def test_open_ticket_review_needs_no_inputs(runner, session: AsyncSession) -> None:
    await TicketService(session).create(
        customer_email="a@example.com", subject="s", body="b"
    )

    run, outputs = await runner.run(library.get("open_ticket_review"))

    assert run.completed is True
    assert len(outputs["open"]) == 1


# --------------------------------------------------------------------------
# Failure behaviour against real tools
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_missing_ticket_stops_the_workflow(runner) -> None:
    run, _ = await runner.run(
        library.get("ticket_context"), inputs={"ticket_id": str(uuid.uuid4())}
    )

    assert run.completed is False
    assert run.failed_step == "ticket"
    assert run.steps[0].outcome is ToolOutcome.FAILED


@pytest.mark.anyio
async def test_a_missing_input_fails_the_step_not_the_engine(runner) -> None:
    run, _ = await runner.run(library.get("ticket_context"))

    assert run.completed is False
    assert run.steps[0].outcome is ToolOutcome.INVALID_PARAMS


@pytest.mark.anyio
async def test_knowledge_lookup_on_an_empty_corpus_still_completes(runner) -> None:
    """Finding nothing is a valid result, not a failure."""
    run, outputs = await runner.run(
        library.get("knowledge_lookup"), inputs={"question": "anything"}
    )

    assert run.completed is True
    assert outputs["passages"] == []


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_run_summary_leaks_no_customer_content(
    runner, session: AsyncSession
) -> None:
    ticket = await TicketService(session).create(
        customer_email="private@example.com",
        subject="Card 4242 charged",
        body="Call 555-0100",
    )

    run, outputs = await runner.run(
        library.get("ticket_context"), inputs={"ticket_id": str(ticket.id)}
    )

    rendered = str(run.model_dump())
    for leak in ("4242", "555-0100", "private@example.com", "postgresql"):
        assert leak not in rendered, leak
    assert "4242" in str(outputs)  # the caller still receives the data


UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
"""Random identifiers, which carry none of the customer's data."""


@pytest.mark.anyio
async def test_logs_record_the_run_shape_not_its_content(
    runner, session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    ticket = await TicketService(session).create(
        customer_email="a@example.com", subject="Card 4242", body="b"
    )

    with caplog.at_level(logging.INFO, logger="app.workflows.execution"):
        await runner.run(
            library.get("ticket_context"), inputs={"ticket_id": str(ticket.id)}
        )

    assert "workflow=ticket_context" in caplog.text
    # Identifiers removed before searching. A run id is a random UUID, and a
    # UUID is hexadecimal -- so it contains every decimal digit and will
    # eventually contain any four of them in a row. CI caught one that did.
    # Matching there is a coincidence, not a leak: what this asserts is that
    # the ticket's own content never reaches a log line.
    logged = UUID_PATTERN.sub("<id>", caplog.text)
    assert "4242" not in logged
