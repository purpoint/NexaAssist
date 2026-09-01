"""Ticket endpoints against a real database."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

from .conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("clean_tables")

TICKETS = "/api/v1/tickets"
PAYLOAD = {"customer_email": "ada@example.com", "subject": "Charged twice", "body": "Refund please."}


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(database_url=TEST_DATABASE_URL, llm_provider="static")
    from app.db import engine as engine_module
    from app.db import session as session_module

    engine_module.get_engine.cache_clear()
    session_module.get_sessionmaker.cache_clear()
    original = engine_module.get_engine
    built = engine_module.build_engine(settings)
    session_module.get_engine = lambda: built  # type: ignore[assignment]
    session_module.get_sessionmaker.cache_clear()
    try:
        with TestClient(create_app(settings)) as test_client:
            yield test_client
    finally:
        session_module.get_engine = original  # type: ignore[assignment]
        session_module.get_sessionmaker.cache_clear()


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


def test_create_returns_201_and_the_ticket(client: TestClient) -> None:
    response = client.post(TICKETS, json=PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["subject"] == "Charged twice"
    assert body["status"] == "open"
    uuid.UUID(body["id"])
    uuid.UUID(body["customer_id"])


def test_created_ticket_is_readable_afterwards(client: TestClient) -> None:
    created = client.post(TICKETS, json=PAYLOAD).json()

    fetched = client.get(f"{TICKETS}/{created['id']}")

    assert fetched.status_code == 200
    assert fetched.json() == created


def test_two_tickets_from_one_email_share_a_customer(client: TestClient) -> None:
    first = client.post(TICKETS, json=PAYLOAD).json()
    second = client.post(TICKETS, json={**PAYLOAD, "subject": "again"}).json()

    assert first["customer_id"] == second["customer_id"]


@pytest.mark.parametrize(
    "override",
    [
        {"customer_email": "nope"},
        {"subject": ""},
        {"body": ""},
        {"subject": "x" * 201},
    ],
)
def test_invalid_payloads_are_422(client: TestClient, override: dict[str, str]) -> None:
    assert client.post(TICKETS, json={**PAYLOAD, **override}).status_code == 422


def test_unknown_field_is_422(client: TestClient) -> None:
    assert client.post(TICKETS, json={**PAYLOAD, "priority": "high"}).status_code == 422


# --------------------------------------------------------------------------
# get
# --------------------------------------------------------------------------


def test_unknown_ticket_is_404_with_the_shared_envelope(client: TestClient) -> None:
    response = client.get(f"{TICKETS}/{uuid.uuid4()}")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "ticket_not_found"
    assert set(body) <= {"code", "message", "details"}


def test_malformed_ticket_id_is_422(client: TestClient) -> None:
    assert client.get(f"{TICKETS}/not-a-uuid").status_code == 422


def test_error_body_exposes_no_internals(client: TestClient) -> None:
    rendered = str(client.get(f"{TICKETS}/{uuid.uuid4()}").json())

    for leak in ("postgresql", "asyncpg", "Traceback", "5432", "SELECT"):
        assert leak not in rendered


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


def test_list_returns_newest_first(client: TestClient) -> None:
    for subject in ("one", "two", "three"):
        client.post(TICKETS, json={**PAYLOAD, "subject": subject})

    body = client.get(TICKETS).json()

    assert [t["subject"] for t in body["items"]][0] == "three"
    assert body["limit"] == 20 and body["offset"] == 0


def test_list_filters_by_status(client: TestClient) -> None:
    client.post(TICKETS, json=PAYLOAD)

    assert len(client.get(TICKETS, params={"status": "open"}).json()["items"]) == 1
    assert client.get(TICKETS, params={"status": "closed"}).json()["items"] == []


def test_list_rejects_an_unknown_status(client: TestClient) -> None:
    assert client.get(TICKETS, params={"status": "banana"}).status_code == 422


def test_list_paginates(client: TestClient) -> None:
    for index in range(5):
        client.post(TICKETS, json={**PAYLOAD, "subject": f"s{index}"})

    first = client.get(TICKETS, params={"limit": 2, "offset": 0}).json()["items"]
    second = client.get(TICKETS, params={"limit": 2, "offset": 2}).json()["items"]

    assert len({t["id"] for t in first + second}) == 4


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_list_rejects_out_of_range_pagination(
    client: TestClient, params: dict[str, int]
) -> None:
    assert client.get(TICKETS, params=params).status_code == 422


# --------------------------------------------------------------------------
# Untouched contracts
# --------------------------------------------------------------------------


def test_existing_endpoints_still_work(client: TestClient) -> None:
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/ready").status_code == 200
