"""What an intent handler is.

A handler takes a classified customer message and produces a reply. Which
handler runs is the router's business; how a reply is produced is the
handler's.

Handlers are declared as a Protocol for the same reason providers and tools
are: a test double satisfies it structurally, and a later handler need not
inherit from anything.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.intent import IntentAnalysis


class HandlerRequest(BaseModel):
    """What a handler is given."""

    model_config = ConfigDict(frozen=True)

    message: str = Field(min_length=1)
    analysis: IntentAnalysis


class HandlerResponse(BaseModel):
    """What a handler produces."""

    model_config = ConfigDict(frozen=True)

    handler: str
    reply: str = Field(min_length=1)
    handled: bool = Field(
        default=True,
        description="False when the handler ran but could not resolve the request.",
    )


@runtime_checkable
class IntentHandler(Protocol):
    """Produces a reply for a classified message."""

    name: str

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        ...
