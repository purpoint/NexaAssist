"""Streaming an answer over a live connection.

Composition, not new capability: it joins the M14 transport to the M14
streaming provider and adds only the rules that exist because the two are
combined.

Questions run through the same pipeline as ``POST /api/v1/assistant/messages``
-- classified, answered from retrieved sources, checked by policy, escalated if
a person is needed -- and the finished answer is then sent as deltas.

That ordering is forced, not chosen. Policy can replace a reply outright: a
billing question is answered by a human, not by whatever the model was midway
through saying. Streaming tokens as they are generated would mean sending text
that policy has not seen yet and may overrule, so the client would have to
watch an answer retract itself. Deltas here are therefore a presentation of a
finished answer rather than a view of one being produced -- the client keeps
its incremental rendering, and what it renders is the answer that was actually
approved.

Without a database there is no retrieval, no conversation and no review queue,
so the socket falls back to streaming unsourced prose from the model. That
answer is marked ``grounded=False`` rather than dressed up as a sourced one: a
guess and a citation look identical in a chat bubble, and the difference is
the client's to show.

One stream at a time per connection. Without that, a client could open a single
socket and start an unbounded number of concurrent model calls, which is a
cheaper way to exhaust the process than opening connections -- those at least
are counted.
"""

import re
import uuid
from collections.abc import Iterator

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.llm.base import LLMPrompt
from app.llm.errors import LLMError
from app.llm.prompts import REALTIME_REPLY_PROMPT_VERSION, REALTIME_REPLY_SYSTEM_PROMPT
from app.llm.streaming import StreamingLLMProvider
from app.models.conversation import MessageRole
from app.realtime.connection import Connection
from app.realtime.conversations import TurnRecorder
from app.realtime.pipeline import Answerer
from app.realtime.envelope import Complete, Delta, Error

logger = get_logger(__name__)

BUSY_CODE = "realtime_busy"
BUSY_MESSAGE = "A question is already being answered on this connection."

STREAM_FAILED_MESSAGE = "The answer could not be completed."

NO_RECORDER_CODE = "realtime_conversations_unavailable"
NO_RECORDER_MESSAGE = "This connection cannot record conversations."

PIPELINE_FAILED_CODE = "realtime_answer_failed"

RECORD_FAILED_CODE = "realtime_turn_not_recorded"
RECORD_FAILED_MESSAGE = "The exchange could not be recorded."


def chunk(text: str, size: int = 24) -> Iterator[str]:
    """Split a finished answer on word boundaries, keeping the whitespace.

    Joining the pieces reproduces the text exactly, which is the property the
    frame contract promises. Whitespace stays on the piece before it rather
    than being stripped: an answer whose chunks lose the spaces between them
    looks fine in a test that counts them and wrong in every rendering.
    """
    if not text:
        return
    piece = ""
    for word in re.split(r"(\s+)", text):
        piece += word
        if len(piece) >= size and not word.isspace():
            yield piece
            piece = ""
    if piece:
        yield piece


class AnswerStreamer:
    """Answers questions on one connection, one at a time."""

    def __init__(
        self,
        provider: StreamingLLMProvider,
        recorder: TurnRecorder | None = None,
        answerer: Answerer | None = None,
    ) -> None:
        self._provider = provider
        self._recorder = recorder
        self._answerer = answerer
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    async def answer(
        self,
        connection: Connection,
        question: str,
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> None:
        """Stream one answer, reporting any failure as a frame.

        Nothing raises out of here. The caller is a socket loop, and an
        escaping exception would end the connection over a failure that
        concerns one question.

        When a conversation is given the exchange is recorded the same way the
        HTTP path records it: the question before the stream starts, the answer
        after it finishes. A conversation that does not exist is reported as an
        error frame and no stream is started -- answering into a conversation
        that cannot hold the reply would produce a turn the client can never
        read back.
        """
        if self._busy:
            await connection.send(Error(code=BUSY_CODE, message=BUSY_MESSAGE))
            return

        self._busy = True
        try:
            if self._answerer is not None and self._answerer.available:
                await self._answer_grounded(connection, question, conversation_id)
            else:
                await self._stream_prose(connection, question, conversation_id)
        finally:
            self._busy = False

    async def _answer_grounded(
        self,
        connection: Connection,
        question: str,
        conversation_id: uuid.UUID | None,
    ) -> None:
        """Run the pipeline, then send the finished answer as deltas.

        The pipeline records both turns itself when it is given a
        conversation, so nothing here records them -- doing both would write
        every exchange twice.
        """
        try:
            reply = await self._answerer.answer(
                question, conversation_id=conversation_id
            )
        except AppError as exc:
            # An unknown conversation is the ordinary case, and it is the
            # client's mistake; its own code travels back unchanged.
            logger.info(
                "realtime answer refused id=%s error_category=%s",
                connection.id,
                exc.code,
            )
            await connection.send(Error(code=exc.code, message=exc.message))
            return
        except Exception as exc:
            # Type only: a pipeline failure's message can name a model, a
            # table, or a connection string.
            logger.warning(
                "realtime answer failed id=%s error=%s",
                connection.id,
                type(exc).__name__,
            )
            await connection.send(
                Error(code=PIPELINE_FAILED_CODE, message=STREAM_FAILED_MESSAGE)
            )
            return

        pieces = list(chunk(reply.reply))
        for piece in pieces:
            await connection.send(Delta(text=piece))

        # Categories, flags and counts. Never the question or the answer.
        logger.info(
            "realtime answer grounded id=%s intent=%s handler=%s handled=%s "
            "escalated=%s citations=%d deltas=%d",
            connection.id,
            reply.intent.value,
            reply.handler,
            reply.handled,
            reply.escalated,
            len(reply.citations),
            len(pieces),
        )
        await connection.send(
            Complete(
                text=reply.reply,
                deltas=len(pieces),
                conversation_id=conversation_id,
                grounded=True,
                citations=list(reply.citations),
                escalated=reply.escalated,
            )
        )

    async def _stream_prose(
        self,
        connection: Connection,
        question: str,
        conversation_id: uuid.UUID | None,
    ) -> None:
        """The fallback: unsourced prose, streamed as the model produces it.

        Reached only when the pipeline cannot run at all, which in practice
        means no database is configured. It records its own turns, because
        there is no pipeline here to do it.
        """
        if conversation_id is not None and not await self._record(
            connection, conversation_id, MessageRole.CUSTOMER, question
        ):
            return

        pieces: list[str] = []
        try:
            prompt = LLMPrompt(system=REALTIME_REPLY_SYSTEM_PROMPT, user=question)
            async for delta in self._provider.stream_text(prompt=prompt):
                pieces.append(delta)
                await connection.send(Delta(text=delta))
        except LLMError as exc:
            # Category only. The provider's own message can name the model, the
            # endpoint, and occasionally the key.
            logger.warning(
                "realtime answer failed id=%s error_category=%s",
                connection.id,
                exc.code,
            )
            await connection.send(Error(code=exc.code, message=STREAM_FAILED_MESSAGE))
            return

        answer = "".join(pieces)
        if conversation_id is not None:
            # The stream already reached the client, so a recording failure
            # here must not retract it: report and still send the completion.
            await self._record(
                connection, conversation_id, MessageRole.ASSISTANT, answer
            )

        # Counts and a prompt version, never the question or the answer.
        logger.info(
            "realtime answer streamed id=%s deltas=%d prompt=%s",
            connection.id,
            len(pieces),
            REALTIME_REPLY_PROMPT_VERSION,
        )
        await connection.send(
            Complete(
                text=answer,
                deltas=len(pieces),
                conversation_id=conversation_id,
                grounded=False,
            )
        )

    async def _record(
        self,
        connection: Connection,
        conversation_id: uuid.UUID,
        role: MessageRole,
        content: str,
    ) -> bool:
        """Append one turn, reporting a failure as a frame. Never raises."""
        if self._recorder is None:
            await connection.send(
                Error(
                    code=NO_RECORDER_CODE,
                    message=NO_RECORDER_MESSAGE,
                )
            )
            return False
        try:
            await self._recorder.record(conversation_id, role, content)
        except AppError as exc:
            # An unknown conversation is the ordinary case here, and it is the
            # client's mistake; its own code travels back unchanged.
            logger.info(
                "realtime turn not recorded id=%s error_category=%s",
                connection.id,
                exc.code,
            )
            await connection.send(Error(code=exc.code, message=exc.message))
            return False
        except Exception as exc:
            # Type only, and never the turn's content.
            logger.warning(
                "realtime turn failed id=%s error=%s",
                connection.id,
                type(exc).__name__,
            )
            await connection.send(
                Error(code=RECORD_FAILED_CODE, message=RECORD_FAILED_MESSAGE)
            )
            return False
        return True
