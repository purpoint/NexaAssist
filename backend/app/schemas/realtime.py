"""Schemas for the realtime ticket endpoint."""

from pydantic import BaseModel, ConfigDict, Field


class RealtimeTicketResponse(BaseModel):
    """A short-lived credential for one WebSocket handshake."""

    model_config = ConfigDict(frozen=True)

    ticket: str = Field(
        description=(
            "Spend this on the handshake as ?ticket=… . Single-use and "
            "short-lived: it is destroyed when redeemed, so a fresh one is "
            "needed for every connection, including a reconnect."
        )
    )
    expires_in_seconds: int = Field(
        ge=1,
        description="How long it stays valid if unused.",
    )
