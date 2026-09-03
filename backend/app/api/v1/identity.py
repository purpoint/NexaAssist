"""Where a request's identity comes from.

The HTTP half of authentication, kept out of ``app.auth`` so that package
stays transport-agnostic and testable without FastAPI -- the same separation
the realtime layer uses for its database access.

``APIKeyHeader`` with ``auto_error=False`` on purpose: the decision about a
missing credential belongs to the authenticator, not to the extractor. With
auto-error on, a deployment running without authentication would start
rejecting requests that the anonymous authenticator is meant to accept.
"""

from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from app.auth.base import Authenticator
from app.auth.factory import get_authenticator
from app.auth.identity import RequestIdentity

API_KEY_HEADER = "X-API-Key"

_api_key = APIKeyHeader(
    name=API_KEY_HEADER,
    auto_error=False,
    description=(
        "Shared key, when the deployment is configured to require one. "
        "Ignored when authentication is disabled."
    ),
)


async def require_identity(
    presented: Annotated[str | None, Security(_api_key)] = None,
    authenticator: Annotated[Authenticator, Depends(get_authenticator)] = None,
) -> RequestIdentity:
    """The identity behind this request.

    Named "require" because it will refuse a request when the configured
    authenticator protects anything. Where authentication is disabled it
    returns an anonymous identity instead of refusing, which is what keeps the
    pre-M19 behaviour intact.
    """
    return await authenticator.authenticate(presented)
