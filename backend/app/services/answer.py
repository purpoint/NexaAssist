"""Grounded answers: retrieve, then answer only from what was retrieved.

Composes :class:`~app.services.document.DocumentService` with the
``LLMProvider`` protocol. It knows nothing about Groq or FastAPI.
"""

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.llm.base import LLMPrompt, LLMProvider
from app.llm.prompts import (
    GROUNDED_ANSWER_PROMPT_VERSION,
    GROUNDED_ANSWER_SYSTEM_PROMPT,
)
from app.schemas.document import Citation, GroundedAnswer
from app.services.document import DocumentService, RetrievedChunk

logger = get_logger(__name__)

NO_SOURCES_ANSWER = "I do not have any documentation that covers that question."


class GroundedModelAnswer(BaseModel):
    """What the model returns: prose plus the source numbers it used.

    Citations are rebuilt from the retrieved chunks rather than trusted from
    the model, so a hallucinated document title cannot reach a reader.
    """

    answered: bool
    answer: str = Field(min_length=1, max_length=2_000)
    cited_sources: list[int] = Field(default_factory=list)


class AnswerService:
    """Answers questions using only the knowledge base."""

    def __init__(self, documents: DocumentService, provider: LLMProvider) -> None:
        self._documents = documents
        self._provider = provider

    async def answer(self, question: str, *, top_k: int = 4) -> GroundedAnswer:
        chunks = await self._documents.search(question, top_k=top_k)
        if not chunks:
            # No retrieval means no grounding. Asking the model anyway would
            # produce exactly the ungrounded answer this endpoint exists to
            # avoid.
            logger.info("grounded answer skipped reason=no_sources")
            return GroundedAnswer(answered=False, answer=NO_SOURCES_ANSWER, citations=[])

        completion = await self._provider.complete_structured(
            prompt=LLMPrompt(
                system=GROUNDED_ANSWER_SYSTEM_PROMPT,
                user=_render_question(question, chunks),
            ),
            schema=GroundedModelAnswer,
        )
        model_answer = completion.output

        citations = [
            _to_citation(chunks[number - 1])
            for number in dict.fromkeys(model_answer.cited_sources)
            if 1 <= number <= len(chunks)
        ]

        logger.info(
            "grounded answer provider=%s model=%s prompt_version=%s sources=%d cited=%d answered=%s",
            completion.provider,
            completion.model,
            GROUNDED_ANSWER_PROMPT_VERSION,
            len(chunks),
            len(citations),
            model_answer.answered,
        )
        return GroundedAnswer(
            answered=model_answer.answered,
            answer=model_answer.answer,
            citations=citations,
        )


def _render_question(question: str, chunks: list[RetrievedChunk] | tuple[RetrievedChunk, ...]) -> str:
    sources = "\n\n".join(
        f"[{index}] {chunk.document_title}\n{chunk.content}"
        for index, chunk in enumerate(chunks, start=1)
    )
    return f"Sources:\n\n{sources}\n\nQuestion: {question}"


def _to_citation(chunk: RetrievedChunk) -> Citation:
    return Citation(
        document_id=chunk.document_id,
        document_title=chunk.document_title,
        ordinal=chunk.ordinal,
        excerpt=chunk.content[:300],
        similarity=chunk.similarity,
    )


STATIC_MODEL_ANSWER = GroundedModelAnswer(
    answered=False,
    answer="Static provider response; no model was called.",
    cited_sources=[],
)
"""Canned answer served when ``LLM_PROVIDER=static``.

Deliberately ``answered=False`` with no citations, so it is obvious at a glance
that nothing was actually reasoned over.
"""
