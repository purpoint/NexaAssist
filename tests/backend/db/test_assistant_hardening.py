"""End-to-end hardening of the assistant API: concurrency, errors, and leakage."""

import asyncio
import logging
import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.loop import AgentDecision
from app.core.config import Settings
from app.db.errors import DatabaseUnavailableError
from app.llm.base import LLMConfig
from app.llm.errors import LLMUnavailableError
from app.llm.providers.static_provider import StaticLLMProvider
from app.main import create_app
from app.rag.embeddings import HashingEmbeddingProvider
from app.schemas.intent import IntentAnalysis, IntentCategory
from app.services.answer import GroundedModelAnswer

from .conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("clean_tables")

MESSAGES = "/api/v1/assistant/messages"
CONVERSATIONS = "/api/v1/conversations"
SECRET_QUESTION = "my card 4111111111111111 was charged twice"
SECRET_REPLY = "I can see the duplicate charge on your account."

ANALYSIS = IntentAnalysis(
    intent=IntentCategory.BILLING, confidence=0.95, reason="fixture"
)


def canned() -> StaticLLMProvider:
    return StaticLLMProvider(
        LLMConfig(provider="static", model="static-model"),
        canned={
            IntentAnalysis: ANALYSIS,
            AgentDecision: AgentDecision(action="answer", answer=SECRET_REPLY),
            GroundedModelAnswer: GroundedModelAnswer(
                answered=True, answer=SECRET_REPLY, cited_sources=[]
            ),
        },
    )


def build(provider: object | None = None) -> tuple[FastAPI, callable]:
    """An app wired to the test database, and the teardown that unpatches it."""
    settings = Settings(database_url=TEST_DATABASE_URL, embedding_provider="hashing")
    from app.db import health as health_module
    from app.db import session as session_module
    from app.db.engine import build_engine
    from app.llm.factory import get_llm_provider
    from app.rag.factory import get_embedding_provider

    built = build_engine(settings)
    originals = (session_module.get_engine, health_module.get_engine)
    session_module.get_engine = lambda: built  # type: ignore[assignment]
    health_module.get_engine = lambda: built  # type: ignore[assignment]
    session_module.get_sessionmaker.cache_clear()

    app = create_app(settings)
    app.dependency_overrides[get_embedding_provider] = HashingEmbeddingProvider
    app.dependency_overrides[get_llm_provider] = lambda: provider or canned()

    def teardown() -> None:
        session_module.get_engine, health_module.get_engine = originals  # type: ignore[assignment]
        session_module.get_sessionmaker.cache_clear()

    return app, teardown


@pytest.fixture
def app() -> Iterator[FastAPI]:
    built, teardown = build()
    try:
        yield built
    finally:
        teardown()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# Concurrency


@pytest.mark.anyio
async def test_concurrent_requests_get_distinct_traces(app: FastAPI) -> None:
    """Correlation must survive requests overlapping in one process."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        responses = await asyncio.gather(
            *(client.post(MESSAGES, json={"message": f"question {n}"}) for n in range(6))
        )

    assert {r.status_code for r in responses} == {200}
    traces = [r.json()["trace_id"] for r in responses]
    assert len(set(traces)) == len(traces), "each request gets its own trace"


@pytest.mark.anyio
async def test_concurrent_conversations_do_not_interleave(app: FastAPI) -> None:
    """Two conversations advanced at once must not adopt each other's turns."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        opened = await asyncio.gather(
            *(
                client.post(CONVERSATIONS, json={"customer_email": f"p{n}@example.com"})
                for n in range(3)
            )
        )
        ids = [r.json()["id"] for r in opened]
        await asyncio.gather(
            *(
                client.post(MESSAGES, json={"message": f"m{n}", "conversation_id": cid})
                for n, cid in enumerate(ids)
            )
        )
        histories = await asyncio.gather(
            *(client.get(f"{CONVERSATIONS}/{cid}/messages") for cid in ids)
        )

    for n, history in enumerate(histories):
        messages = history.json()["messages"]
        assert [m["role"] for m in messages] == ["customer", "assistant"]
        assert messages[0]["content"] == f"m{n}"


# --------------------------------------------------------------------------
# Error mapping


def test_a_provider_outage_is_a_503_not_a_500() -> None:
    class Failing:
        name = "failing"

        async def complete_structured(self, **kwargs: object) -> object:
            raise LLMUnavailableError()

    built, teardown = build(Failing())
    try:
        with TestClient(built) as client:
            response = client.post(MESSAGES, json={"message": "hello"})
    finally:
        teardown()

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == LLMUnavailableError.code
    assert set(body) <= {"code", "message", "details"}


def test_an_unknown_conversation_is_a_404(client: TestClient) -> None:
    response = client.post(
        MESSAGES, json={"message": "hi", "conversation_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "conversation_not_found"


def test_a_malformed_body_is_a_422_before_anything_runs(client: TestClient) -> None:
    assert client.post(MESSAGES, json={"message": ""}).status_code == 422


def test_readiness_reports_unavailable_when_the_database_is_down() -> None:
    """Liveness stays up; readiness is what a load balancer acts on."""
    settings = Settings(
        database_url="postgresql+asyncpg://localhost:59999/nexaassist_test"
    )
    from app.db import health as health_module
    from app.db.engine import build_engine

    built = build_engine(settings)
    original = health_module.get_engine
    health_module.get_engine = lambda: built  # type: ignore[assignment]
    try:
        app = create_app(settings)
        with TestClient(app) as client:
            ready = client.get("/api/v1/ready")
            live = client.get("/api/v1/health")
    finally:
        health_module.get_engine = original  # type: ignore[assignment]

    assert ready.status_code == DatabaseUnavailableError.status_code == 503
    assert live.status_code == 200, "a dependency outage must not fail liveness"


# --------------------------------------------------------------------------
# Nothing leaks


def test_no_log_line_carries_the_message_or_the_reply(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = client.post(MESSAGES, json={"message": SECRET_QUESTION})

    assert response.status_code == 200
    assert "4111111111111111" not in caplog.text
    assert "charged twice" not in caplog.text
    assert SECRET_REPLY not in caplog.text


def test_no_span_carries_the_message_or_the_reply(client: TestClient) -> None:
    """The M16 spans emitted during a real request stay content-free."""
    from app.observability.tracer import InMemoryRecorder, Tracer

    recorder = InMemoryRecorder()
    from app.api.v1.assistant import get_tracer

    client.app.dependency_overrides[get_tracer] = lambda: Tracer(recorder)
    try:
        client.post(MESSAGES, json={"message": SECRET_QUESTION})
    finally:
        client.app.dependency_overrides.pop(get_tracer, None)

    dumped = " ".join(span.model_dump_json() for span in recorder.spans)
    assert recorder.spans, "the request produced spans"
    assert "4111111111111111" not in dumped
    assert SECRET_REPLY not in dumped


def test_an_error_body_carries_no_internals(client: TestClient) -> None:
    body = client.post(
        MESSAGES, json={"message": "hi", "conversation_id": str(uuid.uuid4())}
    ).json()
    rendered = str(body)
    assert "Traceback" not in rendered
    assert "postgresql" not in rendered
    assert "sqlalchemy" not in rendered.lower()


# --------------------------------------------------------------------------
# Regression against the earlier milestones


def test_every_earlier_endpoint_still_answers(client: TestClient) -> None:
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/v1/ready").status_code == 200
    assert client.post("/api/v1/intent/analyze", json={"message": "hi"}).status_code == 200
    assert client.get("/api/v1/tickets").status_code == 200
    assert client.get("/api/v1/documents").status_code == 200


def test_ticket_creation_still_works(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tickets",
        json={
            "customer_email": "person@example.com",
            "subject": "Invoice",
            "body": "Where is it?",
        },
    )
    assert response.status_code == 201


def test_the_realtime_socket_still_greets(client: TestClient) -> None:
    """M14 is untouched by the new HTTP surface."""
    with client.websocket_connect("/api/v1/ws") as socket:
        assert socket.receive_json()["type"] == "ready"
