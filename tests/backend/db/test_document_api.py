"""Document endpoints against a real database."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.rag.embeddings import HashingEmbeddingProvider
from app.services.answer import GroundedModelAnswer
from tests.backend.llm.fakes import FakeLLMProvider

from .conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("clean_tables")

DOCUMENTS = "/api/v1/documents"
REFUNDS = {"title": "Refunds", "content": "Refunds take 5 business days.\n\nContact billing."}


@pytest.fixture
def model_answer() -> GroundedModelAnswer:
    return GroundedModelAnswer(answered=True, answer="Five business days.", cited_sources=[1])


@pytest.fixture
def client(model_answer: GroundedModelAnswer) -> Iterator[TestClient]:
    settings = Settings(database_url=TEST_DATABASE_URL, embedding_provider="hashing")
    from app.db import session as session_module
    from app.db.engine import build_engine
    from app.llm.factory import get_llm_provider
    from app.rag.factory import get_embedding_provider

    built = build_engine(settings)
    original = session_module.get_engine
    session_module.get_engine = lambda: built  # type: ignore[assignment]
    session_module.get_sessionmaker.cache_clear()

    app = create_app(settings)
    app.dependency_overrides[get_embedding_provider] = HashingEmbeddingProvider
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(output=model_answer)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        session_module.get_engine = original  # type: ignore[assignment]
        session_module.get_sessionmaker.cache_clear()


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------


def test_ingest_returns_201(client: TestClient) -> None:
    response = client.post(DOCUMENTS, json=REFUNDS)

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Refunds"
    uuid.UUID(body["id"])


def test_response_hides_chunking_details(client: TestClient) -> None:
    """Chunks are an internal detail of retrieval, not part of the contract."""
    body = client.post(DOCUMENTS, json=REFUNDS).json()

    assert set(body) == {"id", "title", "created_at", "updated_at"}


@pytest.mark.parametrize("override", [{"title": ""}, {"content": ""}, {"title": "x" * 301}])
def test_invalid_ingest_is_422(client: TestClient, override: dict[str, str]) -> None:
    assert client.post(DOCUMENTS, json={**REFUNDS, **override}).status_code == 422


def test_unknown_field_is_422(client: TestClient) -> None:
    assert client.post(DOCUMENTS, json={**REFUNDS, "tags": []}).status_code == 422


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------


def test_get_and_list(client: TestClient) -> None:
    created = client.post(DOCUMENTS, json=REFUNDS).json()

    assert client.get(f"{DOCUMENTS}/{created['id']}").json()["id"] == created["id"]
    listing = client.get(DOCUMENTS).json()
    assert [d["id"] for d in listing["items"]] == [created["id"]]
    assert listing["limit"] == 20


def test_unknown_document_is_404(client: TestClient) -> None:
    response = client.get(f"{DOCUMENTS}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "document_not_found"


def test_list_pagination_is_validated(client: TestClient) -> None:
    assert client.get(DOCUMENTS, params={"limit": 0}).status_code == 422
    assert client.get(DOCUMENTS, params={"offset": -1}).status_code == 422


# --------------------------------------------------------------------------
# Grounded answers
# --------------------------------------------------------------------------


def test_answer_is_grounded_and_cited(client: TestClient) -> None:
    client.post(DOCUMENTS, json=REFUNDS)

    response = client.post(f"{DOCUMENTS}/answer", json={"question": "how long do refunds take"})

    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is True
    assert body["answer"] == "Five business days."
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["document_title"] == "Refunds"
    assert 0.0 <= citation["similarity"] <= 1.0


def test_answer_without_any_documents_is_honest(client: TestClient) -> None:
    body = client.post(f"{DOCUMENTS}/answer", json={"question": "anything"}).json()

    assert body["answered"] is False
    assert body["citations"] == []


def test_answer_rejects_an_empty_question(client: TestClient) -> None:
    assert client.post(f"{DOCUMENTS}/answer", json={"question": ""}).status_code == 422


def test_answer_body_exposes_no_internals(client: TestClient) -> None:
    client.post(DOCUMENTS, json=REFUNDS)

    rendered = str(client.post(f"{DOCUMENTS}/answer", json={"question": "refund"}).json())

    for leak in ("postgresql", "asyncpg", "embedding", "vector(", "Traceback"):
        assert leak not in rendered


# --------------------------------------------------------------------------
# Untouched contracts
# --------------------------------------------------------------------------


def test_existing_endpoints_still_work(client: TestClient) -> None:
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/ready").status_code == 200
    assert client.get("/api/v1/tickets").status_code == 200
