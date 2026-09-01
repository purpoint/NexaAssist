"""Connectivity probe for the database.

Separate from ``engine`` because this answers a different question: not "give
me a pool" but "is the thing behind the pool actually reachable right now".

The probe never raises. It reports what it found and leaves the decision about
HTTP status to the layer that owns HTTP.
"""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger
from app.db.engine import get_engine
from app.db.errors import DatabaseNotConfiguredError
from app.schemas.readiness import ComponentStatus

logger = get_logger(__name__)


async def database_status() -> ComponentStatus:
    """Return the current state of the database dependency.

    A real round trip, not a pool inspection: a pooled connection can look
    healthy while the server behind it is gone.
    """
    try:
        engine = get_engine()
    except DatabaseNotConfiguredError:
        return ComponentStatus.NOT_CONFIGURED

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        # Type only. A driver message can carry the host, the user, and
        # occasionally the connection string itself.
        logger.warning("database probe failed error=%s", type(exc).__name__)
        return ComponentStatus.UNAVAILABLE

    return ComponentStatus.OK
