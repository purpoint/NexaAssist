"""FastAPI application entry point.

Run from the repository root:

    uvicorn app.main:app --reload --app-dir backend
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import api_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()

    logging.basicConfig(level=settings.log_level.upper())

    app = FastAPI(
        title=settings.app_name,
        description="Agentic Customer Support & Workflow Automation Platform",
        version=__version__,
        debug=settings.debug,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_prefix)

    return app


app = create_app()
