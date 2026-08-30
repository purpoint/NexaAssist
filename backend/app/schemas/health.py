"""Schemas for the health endpoint."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response body returned by ``GET /health``."""

    status: str
    service: str
    environment: str
    version: str
