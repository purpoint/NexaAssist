"""The handlers M8 routes to.

Each is built from capabilities that already exist: the M5 knowledge base and
the M7 agent. Handlers compose; they do not reimplement.

Nothing here decides policy about escalation, priority, or human review -- those
are later milestones. A handler answers, or says plainly that it could not.
"""

from app.agent.loop import AgentLoop
from app.core.logging import get_logger
from app.routing.handlers import HandlerRequest, HandlerResponse
from app.services.answer import AnswerService

logger = get_logger(__name__)

UNRESOLVED = (
    "I could not find an answer to that in our documentation. "
    "A support agent can pick this up from here."
)


class KnowledgeBaseHandler:
    """Answers from the documentation, with the grounding rules M5 established.

    Used for the intents whose answers are documented rather than
    account-specific.
    """

    name = "knowledge_base"

    def __init__(self, answers: AnswerService, *, top_k: int = 4) -> None:
        self._answers = answers
        self._top_k = top_k

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        answer = await self._answers.answer(request.message, top_k=self._top_k)
        return HandlerResponse(
            handler=self.name,
            reply=answer.answer,
            # The grounded answerer already reports honestly when the sources
            # do not cover the question; that is exactly "not handled".
            handled=answer.answered,
        )


class AgentHandler:
    """Runs the agent, for intents that need to inspect account state."""

    name = "agent"

    def __init__(self, agent: AgentLoop) -> None:
        self._agent = agent

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        outcome = await self._agent.run(request.message)
        return HandlerResponse(
            handler=self.name, reply=outcome.answer, handled=outcome.completed
        )


class FallbackHandler:
    """Where ambiguous, uncategorised, and unmapped messages land.

    Deliberately does not guess. The router reached here because the
    classification could not be acted on, and answering anyway would be acting
    on exactly the guess the threshold exists to reject.
    """

    name = "fallback"

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        return HandlerResponse(handler=self.name, reply=UNRESOLVED, handled=False)
