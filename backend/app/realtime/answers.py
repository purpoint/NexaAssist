"""Streaming an answer over a live connection.

Composition, not new capability: it joins the M14 transport to the M14
streaming provider and adds only the rules that exist because the two are
combined.

This is not the grounded path. ``POST /api/v1/documents/answer`` answers from
retrieved sources and rebuilds citations from retrieval, and citations can only
be built once an answer is whole -- there is no honest way to stream them. What
streams here is prose, and nothing it produces is presented as sourced.

One stream at a time per connection. Without that, a client could open a single
socket and start an unbounded number of concurrent model calls, which is a
cheaper way to exhaust the process than opening connections -- those at least
are counted.
"""

import uuid

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.llm.base import LLMPrompt
from app.llm.errors import LLMError
from app.llm.prompts import REALTIME_REPLY_PROMPT_VERSION, REALTIME_REPLY_SYSTEM_PROMPT
from app.llm.streaming import StreamingLLMProvider
from app.models.conversation import MessageRole
from app.realtime.connection import Connection
from app.realtime.conversations import TurnRecorder
from app.realtime.envelope import Complete, Delta, Error

logger = get_logger(__name__)

BUSY_CODE = "realtime_busy"
BUSY_MESSAGE = "A question is already being answered on this connection."

STREAM_FAILED_MESSAGE = "The answer could not be completed."

NO_RECORDER_CODE = "realtime_conversations_unavailable"
NO_RECORDER_MESSAGE = "This connection cannot record conversations."

RECORD_FAILED_CODE = "realtime_turn_not_recorded"
RECORD_FAILED_MESSAGE = "The exchange could not be recorded."


class AnswerStreamer:
    """Answers questions on one connection, one at a time."""

    def __init__(
        self,
        provider: StreamingLLMProvider,
        recorder: TurnRecorder | None = None,
    ) -> None:
        self._provider = provider
        self._recorder = recorder
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

        if conversation_id is not None and not await self._record(
            connection, conversation_id, MessageRole.CUSTOMER, question
        ):
            return

        self._busy = True
        pieces: list[str] = []
        try:
            prompt = LLMPrompt(
                system=REALTIME_REPLY_SYSTEM_PROMPT, user=question
            )
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
            await connection.send(
                Error(code=exc.code, message=STREAM_FAILED_MESSAGE)
            )
            return
        finally:
            self._busy = False

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
                text=answer, deltas=len(pieces), conversation_id=conversation_id
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
