"""Running the assistant pipeline for a socket.

The socket used to answer with raw model prose: it held a streaming provider
and forwarded whatever came back. That was fast and ungrounded, and it meant
the one behaviour the product exists for -- an answer that cites the document
it came from, or escalates instead of guessing -- was reachable over HTTP and
invisible in the client, which prefers the socket whenever one is open.

This runs the same pipeline the HTTP endpoint runs, over a session that lives
for one question rather than for the connection. That is the same reasoning as
:mod:`app.realtime.conversations`: a socket lives for minutes and a database
session should not, or an idle client pins a pool connection and holds a
transaction open across long gaps.

Together with that module this is the only part of ``app.realtime`` that knows
about the database. The streamer depends on the :class:`Answerer` protocol, so
the transport stays testable without one.
"""

import uuid
from typing import Protocol, runtime_checkable

from app.api.v1.assistant import get_assistant_service
from app.auth.authorization import OwnerScope
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.llm.factory import get_llm_provider
from app.observability.factory import get_metrics, get_pricing_table, get_tracer
from app.rag.factory import get_embedding_provider
from app.services.assistant import AssistantReply

logger = get_logger(__name__)


@runtime_checkable
class Answerer(Protocol):
    """Answers one question through the full pipeline."""

    async def answer(
        self,
        question: str,
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> AssistantReply:
        """Classify, route, ground, apply policy, and decide on escalation."""
        ...

    def scope_to(self, scope: OwnerScope | None) -> None:
        """Bind the answerer to one subject's resources."""
        ...

    @property
    def available(self) -> bool:
        """Whether this can run at all, so a caller can choose another path."""
        ...


class SessionAnswerer:
    """Runs the pipeline in its own session, under this connection's ownership.

    The scope arrives at connect time rather than in the constructor, for the
    same reason the turn recorder's does: it is not known until the handshake's
    ticket has said who is asking.
    """

    def __init__(
        self, scope: OwnerScope | None = None, settings: Settings | None = None
    ) -> None:
        self._scope = scope
        self._settings = settings or get_settings()

    def scope_to(self, scope: OwnerScope | None) -> None:
        self._scope = scope

    @property
    def available(self) -> bool:
        """No database, no pipeline.

        Retrieval, conversations and review items all need one. Asked without
        it, the caller falls back to streaming prose -- which is a worse answer
        and is reported as one rather than dressed up as a sourced reply.
        """
        return self._settings.database_url is not None

    async def answer(
        self,
        question: str,
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> AssistantReply:
        factory = get_sessionmaker()
        async with factory() as session:
            # Assembled exactly as the HTTP endpoint assembles it, by calling
            # the same function. Two ways of building the same pipeline would
            # eventually be two different pipelines.
            service = get_assistant_service(
                session=session,
                embedder=get_embedding_provider(),
                provider=get_llm_provider(),
                settings=self._settings,
                tracer=get_tracer(),
                pricing=get_pricing_table(),
                metrics=get_metrics(),
            )
            # The pipeline records both turns itself when given a conversation,
            # so the streamer must not also record them.
            return await service.respond(
                question, conversation_id=conversation_id, scope=self._scope
            )

