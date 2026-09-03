"""Authenticator registry and dependency.

Adding a mechanism means one entry here and one option on
``Settings.auth_provider``; a test asserts the two stay in step, as it does for
the embedders, job queues, and trace recorders.
"""

from collections.abc import Callable
from functools import lru_cache

from app.auth.authorization import Authorizer, OpenAuthorizer, SubjectScopedAuthorizer
from app.auth.base import Authenticator
from app.auth.providers import (
    AnonymousAuthenticator,
    ApiKeyAuthenticator,
    parse_credentials,
)
from app.core.config import Settings, get_settings


def _build_api_key(settings: Settings) -> Authenticator:
    return ApiKeyAuthenticator(parse_credentials(settings.auth_api_keys))


_AUTHENTICATORS: dict[str, Callable[[Settings], Authenticator]] = {
    AnonymousAuthenticator.name: lambda _settings: AnonymousAuthenticator(),
    ApiKeyAuthenticator.name: _build_api_key,
}

AUTHENTICATOR_NAMES: tuple[str, ...] = tuple(sorted(_AUTHENTICATORS))


def build_authenticator(settings: Settings) -> Authenticator:
    """Construct the authenticator named by settings."""
    return _AUTHENTICATORS[settings.auth_provider](settings)


@lru_cache(maxsize=1)
def _default_authenticator() -> Authenticator:
    return build_authenticator(get_settings())


def get_authenticator() -> Authenticator:
    """The process-wide authenticator.

    Cached because parsing keys on every request is pointless work, and because
    the configured set must not appear to change between requests.
    """
    return _default_authenticator()


_AUTHORIZERS: dict[str, Callable[[], Authorizer]] = {
    OpenAuthorizer.name: OpenAuthorizer,
    SubjectScopedAuthorizer.name: SubjectScopedAuthorizer,
}

AUTHORIZER_NAMES: tuple[str, ...] = tuple(sorted(_AUTHORIZERS))


def build_authorizer(settings: Settings) -> Authorizer:
    """Construct the authorizer named by settings."""
    return _AUTHORIZERS[settings.authz_provider]()


@lru_cache(maxsize=1)
def _default_authorizer() -> Authorizer:
    return build_authorizer(get_settings())


def get_authorizer() -> Authorizer:
    """The process-wide authorizer."""
    return _default_authorizer()
