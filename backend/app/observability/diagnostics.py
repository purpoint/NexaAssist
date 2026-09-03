"""What each dependency looks like right now.

Extends the M3 readiness probe rather than replacing it: the database keeps
exactly the semantics it had -- configured but unreachable is still the one
thing that makes the service unready -- and the other dependencies are
*reported* rather than allowed to take the service out of rotation.

That distinction is the whole design. A queue outage does not stop the service
answering questions, so failing readiness for it would remove a working process
from a load balancer over a degraded feature. Reporting it as degraded tells an
operator what is wrong without pretending the service is dead.

Nothing here probes the model provider over the network. A liveness check that
calls a vendor costs money on every scrape and rate-limits the thing it is
supposed to protect, so the provider is reported by *configuration*: whether a
provider is selected and whether it has what it needs.

No probe ever surfaces a URL, a host, a credential, SQL, or a driver message.
A status and a component name are the entire vocabulary.
"""

from app.core.logging import get_logger
from app.schemas.readiness import ComponentStatus

logger = get_logger(__name__)


async def job_queue_status() -> ComponentStatus:
    """Whether background jobs can be queued.

    ``not_configured`` for the in-memory queue: it always works, but it is not
    a shared queue, and reporting it as healthy would let an operator believe
    a durable one is running.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.job_queue != "redis":
        return ComponentStatus.NOT_CONFIGURED

    try:
        from app.jobs.factory import get_job_queue

        reachable = await get_job_queue().ping()
    except Exception as exc:
        # Type only. A client error can carry the connection string.
        logger.warning("job queue probe failed error=%s", type(exc).__name__)
        return ComponentStatus.DEGRADED

    return ComponentStatus.OK if reachable else ComponentStatus.DEGRADED


def model_provider_status() -> ComponentStatus:
    """Whether a model provider is configured and has its credentials.

    Deliberately not a network call: probing a paid vendor on every readiness
    scrape spends money to learn something a misconfiguration check already
    tells you.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.llm_provider == "static":
        # Deterministic and offline. It works, and an operator should know it
        # is not a real model.
        return ComponentStatus.NOT_CONFIGURED
    if settings.llm_api_key is None:
        # The SDK may still resolve a key from the environment, so this is
        # "we cannot confirm", not "it is broken".
        return ComponentStatus.DEGRADED
    return ComponentStatus.OK


def rate_limiter_status() -> ComponentStatus:
    """Whether request limiting is in force."""
    from app.core.config import get_settings

    provider = get_settings().rate_limit_provider
    if provider == "none":
        return ComponentStatus.NOT_CONFIGURED
    return ComponentStatus.OK


def authentication_status() -> ComponentStatus:
    """Whether the API requires credentials.

    Reported because "this deployment is open" is an operational fact worth
    seeing on a dashboard, not a detail to rediscover during an incident.
    """
    from app.core.config import get_settings

    if get_settings().auth_provider == "none":
        return ComponentStatus.NOT_CONFIGURED
    return ComponentStatus.OK
