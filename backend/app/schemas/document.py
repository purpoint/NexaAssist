"""Document ingestion and grounded-answer API contract."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MAX_TITLE = 300
MAX_CONTENT = 200_000
MAX_QUESTION = 2_000


class DocumentIngestRequest(BaseModel):
    """Body accepted by ``POST /documents``."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    content: str = Field(min_length=1, max_length=MAX_CONTENT)


class DocumentResponse(BaseModel):
    """A stored document. Chunks are an internal detail of retrieval."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    limit: int
    offset: int


class Citation(BaseModel):
    """Provenance for one retrieved span, so a reader can check the claim."""

    document_id: uuid.UUID
    document_title: str
    ordinal: int = Field(description="Position of the chunk within its document.")
    excerpt: str
    similarity: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Cosine similarity to the question. A retrieval score, not a "
            "confidence in the answer."
        ),
    )


class GroundedAnswerRequest(BaseModel):
    """Body accepted by ``POST /documents/answer``."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION)


class GroundedAnswer(BaseModel):
    """An answer composed strictly from retrieved sources."""

    answered: bool = Field(
        description="False when the sources do not contain the answer."
    )
    answer: str = Field(min_length=1, max_length=2_000)
    citations: list[Citation] = Field(
        default_factory=list,
        description="Only the sources the model reported using.",
    )
