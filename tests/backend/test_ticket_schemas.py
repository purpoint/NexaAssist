"""Ticket request/response schemas, offline."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.ticket import TicketStatus
from app.schemas.ticket import (
    MAX_BODY,
    MAX_SUBJECT,
    TicketCreateRequest,
    TicketListResponse,
    TicketResponse,
)

VALID = {"customer_email": "ada@example.com", "subject": "s", "body": "b"}


def test_valid_request() -> None:
    assert TicketCreateRequest(**VALID).customer_email == "ada@example.com"


@pytest.mark.parametrize(
    "override",
    [
        {"customer_email": "not-an-email"},
        {"customer_email": ""},
        {"subject": ""},
        {"body": ""},
        {"subject": "x" * (MAX_SUBJECT + 1)},
        {"body": "x" * (MAX_BODY + 1)},
    ],
)
def test_invalid_requests_are_rejected(override: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        TicketCreateRequest(**{**VALID, **override})


@pytest.mark.parametrize("field", ["customer_email", "subject", "body"])
def test_every_field_is_required(field: str) -> None:
    payload = {k: v for k, v in VALID.items() if k != field}
    with pytest.raises(ValidationError):
        TicketCreateRequest(**payload)


def test_unknown_fields_are_rejected() -> None:
    """A typo should be a 422, not a silently dropped value."""
    with pytest.raises(ValidationError):
        TicketCreateRequest(**VALID, priority="high")


def test_response_reads_from_orm_attributes() -> None:
    now = datetime.now(UTC)
    source = type(
        "Row",
        (),
        {
            "id": uuid.uuid4(),
            "customer_id": uuid.uuid4(),
            "subject": "s",
            "body": "b",
            "status": TicketStatus.OPEN,
            "created_at": now,
            "updated_at": now,
        },
    )()

    response = TicketResponse.model_validate(source)

    assert response.status is TicketStatus.OPEN
    assert response.model_dump()["status"] == "open"


def test_response_exposes_no_customer_object() -> None:
    """The relationship is lazy="raise"; the contract must not need it."""
    assert set(TicketResponse.model_fields) == {
        "id",
        "customer_id",
        "subject",
        "body",
        "status",
        "created_at",
        "updated_at",
    }


def test_list_response_reports_the_page_without_a_total() -> None:
    page = TicketListResponse(items=[], limit=20, offset=0)

    assert set(page.model_dump()) == {"items", "limit", "offset"}
