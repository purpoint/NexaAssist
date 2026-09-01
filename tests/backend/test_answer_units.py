"""Grounded answering, driven offline with a fake provider."""

import logging
import uuid

import pytest
from pydantic import ValidationError

from app.schemas.document import (
    Citation,
    DocumentIngestRequest,
    GroundedAnswer,
    GroundedAnswerRequest,
)
from app.services.answer import (
    NO_SOURCES_ANSWER,
    AnswerService,
    GroundedModelAnswer,
    _render_question,
)
from app.services.document import RetrievedChunk
from tests.backend.llm.fakes import FakeLLMProvider

DOC = uuid.uuid4()


def chunk(ordinal: int, title: str, content: str, distance: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=DOC,
        document_title=title,
        ordinal=ordinal,
        content=content,
        distance=distance,
    )


class StubDocuments:
    def __init__(self, hits: list[RetrievedChunk]) -> None:
        self._hits = hits
        self.queries: list[str] = []

    async def search(self, query: str, *, top_k: int = 4):
        self.queries.append(query)
        return self._hits[:top_k]


def service(hits: list[RetrievedChunk], model_answer: GroundedModelAnswer) -> AnswerService:
    return AnswerService(StubDocuments(hits), FakeLLMProvider(output=model_answer))


HITS = [
    chunk(0, "Refunds", "Refunds take 5 business days.", 0.10),
    chunk(1, "Refunds", "Contact billing to start one.", 0.30),
]


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_answer_cites_the_sources_the_model_used() -> None:
    answer = await service(
        HITS, GroundedModelAnswer(answered=True, answer="Five days.", cited_sources=[1])
    ).answer("how long do refunds take")

    assert answer.answered is True
    assert answer.answer == "Five days."
    assert [c.ordinal for c in answer.citations] == [0]
    assert answer.citations[0].document_title == "Refunds"


@pytest.mark.anyio
async def test_no_sources_means_no_model_call() -> None:
    """Asking without grounding would produce exactly what this avoids."""
    provider = FakeLLMProvider(output=GroundedModelAnswer(answered=True, answer="x"))
    result = await AnswerService(StubDocuments([]), provider).answer("anything")

    assert provider.call_count == 0
    assert result.answered is False
    assert result.answer == NO_SOURCES_ANSWER
    assert result.citations == []


@pytest.mark.anyio
async def test_citations_are_rebuilt_from_retrieval_not_trusted_from_the_model() -> None:
    """A hallucinated title must not reach a reader."""
    answer = await service(
        HITS,
        GroundedModelAnswer(answered=True, answer="a", cited_sources=[2]),
    ).answer("q")

    assert answer.citations[0].document_title == "Refunds"
    assert answer.citations[0].document_id == DOC


@pytest.mark.anyio
async def test_out_of_range_source_numbers_are_discarded() -> None:
    answer = await service(
        HITS, GroundedModelAnswer(answered=True, answer="a", cited_sources=[0, 7, -1])
    ).answer("q")

    assert answer.citations == []


@pytest.mark.anyio
async def test_duplicate_citations_are_collapsed() -> None:
    answer = await service(
        HITS, GroundedModelAnswer(answered=True, answer="a", cited_sources=[1, 1, 2])
    ).answer("q")

    assert [c.ordinal for c in answer.citations] == [0, 1]


@pytest.mark.anyio
async def test_unanswerable_question_is_reported_as_such() -> None:
    answer = await service(
        HITS, GroundedModelAnswer(answered=False, answer="Not covered.", cited_sources=[])
    ).answer("q")

    assert answer.answered is False
    assert answer.citations == []


@pytest.mark.anyio
async def test_top_k_is_passed_through() -> None:
    documents = StubDocuments(HITS)
    await AnswerService(
        documents, FakeLLMProvider(output=GroundedModelAnswer(answered=True, answer="a"))
    ).answer("question text", top_k=1)

    assert documents.queries == ["question text"]


# --------------------------------------------------------------------------
# Prompt rendering
# --------------------------------------------------------------------------


def test_sources_are_numbered_from_one() -> None:
    rendered = _render_question("why", HITS)

    assert "[1] Refunds" in rendered and "[2] Refunds" in rendered
    assert "Question: why" in rendered


# --------------------------------------------------------------------------
# Similarity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("distance", "expected"), [(0.0, 1.0), (1.0, 0.0), (1.5, 0.0), (-0.2, 1.0)]
)
def test_similarity_is_clamped(distance: float, expected: float) -> None:
    assert chunk(0, "t", "c", distance).similarity == pytest.approx(expected)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


def test_ingest_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DocumentIngestRequest(title="t", content="c", tags=["x"])


@pytest.mark.parametrize("override", [{"title": ""}, {"content": ""}])
def test_ingest_request_requires_text(override: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        DocumentIngestRequest(**{"title": "t", "content": "c", **override})


def test_question_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        GroundedAnswerRequest(question="")


def test_citation_similarity_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Citation(document_id=DOC, document_title="t", ordinal=0, excerpt="e", similarity=1.5)


def test_grounded_answer_defaults_to_no_citations() -> None:
    assert GroundedAnswer(answered=False, answer="a").citations == []


# --------------------------------------------------------------------------
# Logging policy
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_logs_carry_counts_not_question_or_source_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.answer"):
        await service(
            HITS, GroundedModelAnswer(answered=True, answer="a", cited_sources=[1])
        ).answer("my card 4242 was charged")

    assert "sources=2" in caplog.text and "cited=1" in caplog.text
    assert "4242" not in caplog.text
    assert "Refunds take 5 business days." not in caplog.text
