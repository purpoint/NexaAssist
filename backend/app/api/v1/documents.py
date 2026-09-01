"""Knowledge-base endpoints: ingestion, listing, and grounded answers."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.llm.base import LLMProvider
from app.llm.factory import get_llm_provider
from app.rag.embeddings import EmbeddingProvider
from app.rag.factory import get_embedding_provider
from app.schemas.common import ErrorResponse
from app.schemas.document import (
    DocumentIngestRequest,
    DocumentListResponse,
    DocumentResponse,
    GroundedAnswer,
    GroundedAnswerRequest,
)
from app.services.answer import AnswerService
from app.services.document import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_PAGE_SIZE = 100


def get_document_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    embedder: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
) -> DocumentService:
    return DocumentService(session, embedder)


def get_answer_service(
    documents: Annotated[DocumentService, Depends(get_document_service)],
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> AnswerService:
    return AnswerService(documents, provider)


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a knowledge-base document",
)
async def ingest_document(
    payload: DocumentIngestRequest,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    """Chunk, embed, and store a document."""
    document = await service.ingest(title=payload.title, content=payload.content)
    return DocumentResponse.model_validate(document)


@router.post(
    "/answer",
    response_model=GroundedAnswer,
    summary="Answer a question from the knowledge base",
)
async def answer_question(
    payload: GroundedAnswerRequest,
    service: Annotated[AnswerService, Depends(get_answer_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GroundedAnswer:
    """Answer using only retrieved documentation, with citations."""
    return await service.answer(payload.question, top_k=settings.retrieval_top_k)


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Fetch one document",
    responses={404: {"model": ErrorResponse, "description": "No such document."}},
)
async def get_document(
    document_id: uuid.UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    return DocumentResponse.model_validate(await service.get(document_id))


@router.get("", response_model=DocumentListResponse, summary="List documents")
async def list_documents(
    service: Annotated[DocumentService, Depends(get_document_service)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    documents = await service.list(limit=limit, offset=offset)
    return DocumentListResponse(
        items=[DocumentResponse.model_validate(d) for d in documents],
        limit=limit,
        offset=offset,
    )
