"""Aggregate router for version 1 of the API.

Mount new v1 route modules here; ``main.py`` only ever includes this one
router, so the version prefix is applied in exactly one place.
"""

from fastapi import APIRouter

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

router = APIRouter()
router.include_router(health.router)
router.include_router(readiness.router)
router.include_router(intent.router)
router.include_router(tickets.router)
router.include_router(documents.router)
router.include_router(assistant.router)
router.include_router(conversations.router)
router.include_router(realtime.router)
