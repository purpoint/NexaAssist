"""Schemas for the readiness endpoint."""

from enum import StrEnum

from pydantic import BaseModel, Field


class ComponentStatus(StrEnum):
    """State of one dependency the service relies on."""

    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    """Configured, reachable in principle, and not working right now.

    Distinct from ``unavailable``, which is reserved for the dependencies that
    make the service unready. A degraded component is reported and the service
    keeps taking traffic."""


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
    components: dict[str, ComponentStatus] = Field(
        default_factory=dict,
        description=(
            "Every dependency's current state, for an operator rather than a "
            "load balancer. Only the database can make the service unready; "
            "the rest are reported, so a degraded feature does not remove a "
            "working process from rotation."
        ),
    )
