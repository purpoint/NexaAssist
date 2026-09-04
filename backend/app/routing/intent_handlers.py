"""The handlers M8 routes to.

Each is built from capabilities that already exist: the M5 knowledge base and
the M7 agent. Handlers compose; they do not reimplement.

Nothing here decides policy about escalation, priority, or human review -- those
are later milestones. A handler answers, or says plainly that it could not.
"""

from app.agent.loop import AgentLoop
from app.core.logging import get_logger
from app.routing.handlers import HandlerRequest, HandlerResponse, IntentHandler
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
            # M5 rebuilds these from retrieval rather than trusting the model,
            # so they are safe to pass on unchanged.
            citations=list(answer.citations),
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


class DocumentedFirstHandler:
    """Answer from the documentation when it can; otherwise hand on.

    Exists because "this message is about money" and "a person must handle
    this" are not the same statement, and the routing table treated them as
    one. A refund *window* is a policy written down in the knowledge base; a
    refund *request* needs somebody to look at an account. The classifier
    returns ``billing`` for both, so the split cannot be made there -- but it
    can be made here, by asking the documentation first and falling through
    when the documentation does not cover it.

    That works because the grounded answerer already reports honestly rather
    than improvising: ``handled`` is false exactly when retrieval could not
    support an answer, which is the same condition under which the agent
    should get the message.

    Composition, not new capability: it holds two handlers and returns
    whichever one answered, unchanged, so the reply still names the handler
    that produced it and carries its citations.

    The cost is one retrieval attempt on messages that end up at the agent
    anyway. That is the right way round: the alternative spends a person's
    attention on questions a document already answers.
    """

    def __init__(self, documented: "IntentHandler", otherwise: "IntentHandler") -> None:
        self._documented = documented
        self._otherwise = otherwise

    @property
    def name(self) -> str:
        """Reported for the pair; each response still names its own handler."""
        return f"{self._documented.name}+{self._otherwise.name}"

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        documented = await self._documented.handle(request)
        if documented.handled:
            return documented
        logger.info(
            "documented answer unavailable intent=%s handler=%s",
            request.analysis.intent.value,
            self._otherwise.name,
        )
        return await self._otherwise.handle(request)


class FallbackHandler:
    """Where ambiguous, uncategorised, and unmapped messages land.

    Deliberately does not guess. The router reached here because the
    classification could not be acted on, and answering anyway would be acting
    on exactly the guess the threshold exists to reject.
    """

    name = "fallback"

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        return HandlerResponse(handler=self.name, reply=UNRESOLVED, handled=False)
