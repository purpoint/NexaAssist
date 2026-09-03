"""Readiness endpoint.

Distinct from ``/health``, and deliberately not folded into it. Liveness
answers "is this process running" and must keep returning 200 while a
dependency is down, or an orchestrator will restart a process that is working
fine. Readiness answers "should traffic be sent here", and a dependency outage
is exactly when the answer is no.

The existing ``/health`` contract is unchanged.
"""

from fastapi import APIRouter

from app.db.health import database_status
from app.observability.diagnostics import (
    authentication_status,
    job_queue_status,
    model_provider_status,
    rate_limiter_status,
)
from app.db.errors import DatabaseUnavailableError
from app.schemas.common import ErrorResponse
from app.schemas.readiness import ComponentStatus, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Service readiness",
    responses={503: {"model": ErrorResponse, "description": "A dependency is unavailable."}},
)
async def ready() -> ReadinessResponse:
    """Report whether every configured dependency is reachable.

    A database that is configured but unreachable is a 503. A database that was
    never configured is reported as such and does not make the service unready:
    running without one is a supported mode, and the body says so plainly.
    """
    database = await database_status()

    # The status-code decision belongs at the HTTP layer; the probe itself
    # stays transport-agnostic.
    if database is ComponentStatus.UNAVAILABLE:
        raise DatabaseUnavailableError(details={"component": "database"})

    # Reported, never fatal. A queue or a provider being degraded does not
    # stop this process answering, and taking it out of rotation for that
    # would turn a partial outage into a total one.
    return ReadinessResponse(
        database=database,
        components={
            "database": database,
            "job_queue": await job_queue_status(),
            "model_provider": model_provider_status(),
            "rate_limiter": rate_limiter_status(),
            "authentication": authentication_status(),
        },
    )
