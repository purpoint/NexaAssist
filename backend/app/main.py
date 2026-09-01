"""FastAPI application entry point.

Run from the repository root:

    uvicorn app.main:app --reload --app-dir backend

This module stays thin on purpose: it builds the application, configures
middleware and error handling, and mounts routers. Endpoint logic lives in
``app.api``; domain logic lives in ``app.services``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1 import health as v1_health
from app.api.v1.router import router as v1_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.engine import dispose_engine

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()

    # Configured secrets are registered as literals to scrub, so they cannot
    # surface through a third-party traceback or SDK debug logging.
    secrets = [
        secret.get_secret_value()
        for secret in (settings.llm_api_key, settings.database_url)
        if secret is not None
    ]
    configure_logging(settings.log_level, secrets)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "%s %s starting (env=%s, api=%s)",
            settings.app_name,
            __version__,
            settings.app_env,
            settings.api_v1_prefix,
        )
        yield
        # The engine is built lazily, so this is a no-op when nothing ever
        # opened a connection. Migrations are never run from here: schema
        # changes are an explicit operator action.
        await dispose_engine()
        logger.info("%s shutting down", settings.app_name)

    app = FastAPI(
        title=settings.app_name,
        description="Agentic Customer Support & Workflow Automation Platform",
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(v1_router, prefix=settings.api_v1_prefix)

    if settings.enable_legacy_health_route:
        # Pre-v1 alias kept so existing callers of /api/health do not break.
        # Marked deprecated in the OpenAPI schema; scheduled for removal in M18.
        app.include_router(
            v1_health.router,
            prefix=settings.api_prefix,
            tags=["deprecated"],
            deprecated=True,
        )

    return app


app = create_app()
