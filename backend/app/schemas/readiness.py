"""Schemas for the readiness endpoint."""

from enum import StrEnum

from pydantic import BaseModel, Field


class ComponentStatus(StrEnum):
    """State of one dependency the service relies on."""

    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"


class ReadinessResponse(BaseModel):
    """Body returned when the service is ready to take traffic.

    An unready service does not return this shape: it returns the standard
    ``ErrorResponse`` with a 503, so a load balancer can act on the status code
    alone without parsing a body.
    """

    status: str = Field(
        default="ready",
        description="Overall readiness. Always 'ready' when this body is returned.",
    )
    database: ComponentStatus = Field(
        description=(
            "'ok' when a connection succeeded, 'not_configured' when no "
            "DATABASE_URL is set — which is a deliberate operator choice, not a "
            "fault."
        )
    )
