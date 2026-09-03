"""Authentication failures, expressed as application errors."""

from app.core.exceptions import AppError

WWW_AUTHENTICATE = "X-API-Key"
"""Named in the challenge so a client knows *how* to authenticate, not just that it must."""


class AuthenticationError(AppError):
    """Base for every failure to establish an identity.

    A 401 rather than a 403: the request was not authenticated, which is a
    different thing from being authenticated and not allowed. Messages are
    fixed strings -- never an echo of what was presented, because what was
    presented is a credential.
    """

    status_code = 401
    code = "unauthenticated"
    message = "Authentication is required."

    @property
    def headers(self) -> dict[str, str]:
        return {"WWW-Authenticate": WWW_AUTHENTICATE}


class AuthenticationRequiredError(AuthenticationError):
    """No credentials were presented at all."""

    code = "authentication_required"
    message = "Authentication is required."


class InvalidCredentialsError(AuthenticationError):
    """Credentials were presented and did not match.

    Deliberately the same status and shape as the missing case. A client that
    can tell "wrong key" from "no key" learns nothing useful; an attacker
    learns that a key was the right *shape*.
    """

    code = "invalid_credentials"
    message = "Authentication is required."


class AuthenticationConfigurationError(AppError):
    """The configured authenticator cannot be built.

    A 500 raised at construction, so a misconfigured deployment fails while an
    operator is watching rather than on the first protected request.
    """

    status_code = 500
    code = "authentication_configuration_error"
    message = "The authentication configuration is not valid."
