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

from app.core.logging import get_logger
from app.llm.base import LLMPrompt
from app.llm.errors import LLMError
from app.llm.prompts import REALTIME_REPLY_PROMPT_VERSION, REALTIME_REPLY_SYSTEM_PROMPT
from app.llm.streaming import StreamingLLMProvider
from app.realtime.connection import Connection
from app.realtime.envelope import Complete, Delta, Error

logger = get_logger(__name__)

BUSY_CODE = "realtime_busy"
BUSY_MESSAGE = "A question is already being answered on this connection."

STREAM_FAILED_MESSAGE = "The answer could not be completed."


class AnswerStreamer:
    """Answers questions on one connection, one at a time."""

    def __init__(self, provider: StreamingLLMProvider) -> None:
        self._provider = provider
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    async def answer(self, connection: Connection, question: str) -> None:
        """Stream one answer, reporting any failure as a frame.

        Nothing raises out of here. The caller is a socket loop, and an
        escaping exception would end the connection over a failure that
        concerns one question.
        """
        if self._busy:
            await connection.send(Error(code=BUSY_CODE, message=BUSY_MESSAGE))
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

        # Counts and a prompt version, never the question or the answer.
        logger.info(
            "realtime answer streamed id=%s deltas=%d prompt=%s",
            connection.id,
            len(pieces),
            REALTIME_REPLY_PROMPT_VERSION,
        )
        await connection.send(Complete(text="".join(pieces), deltas=len(pieces)))
