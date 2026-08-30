"""Health check endpoint.

Used by local development, tests, and (later) container orchestration to
confirm the service is up and which build it is running.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app import __version__
from app.core.config import Settings, get_settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Report that the service is running."""
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env,
        version=__version__,
    )
