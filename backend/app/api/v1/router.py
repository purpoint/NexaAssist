"""Aggregate router for version 1 of the API.

Mount new v1 route modules here; ``main.py`` only ever includes this one
router, so the version prefix is applied in exactly one place.
"""

from fastapi import APIRouter

from app.schemas.common import ErrorResponse

from app.api.v1 import (
    assistant,
    conversations,
    documents,
    health,
    intent,
    readiness,
    realtime,
    tickets,
)

# Declared once for every v1 route rather than repeated on each: M20 replaced
# FastAPI's default 422 body -- which embeds the offending input, and so
# returned a customer's message back to them -- with the shared error shape,
# and the document must say so.
router = APIRouter(
    responses={
        422: {
            "model": ErrorResponse,
            "description": "The request was not valid. Field paths only.",
        }
    }
)
router.include_router(health.router)
router.include_router(readiness.router)
router.include_router(intent.router)
router.include_router(tickets.router)
router.include_router(documents.router)
router.include_router(assistant.router)
router.include_router(conversations.router)
router.include_router(realtime.router)
