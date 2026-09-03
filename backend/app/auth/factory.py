"""Authenticator registry and dependency.

Adding a mechanism means one entry here and one option on
``Settings.auth_provider``; a test asserts the two stay in step, as it does for
the embedders, job queues, and trace recorders.
"""

from collections.abc import Callable
from functools import lru_cache

from app.auth.authorization import Authorizer, OpenAuthorizer, SubjectScopedAuthorizer
from app.auth.base import Authenticator
from app.auth.tickets import InMemoryTicketStore, TicketStore
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


def _build_redis_tickets(settings: Settings) -> TicketStore:
    # Settings guarantees the URL when this store is selected.
    url = settings.redis_url
    assert url is not None  # noqa: S101 - enforced by Settings validation
    from app.auth.redis_tickets import RedisTicketStore

    return RedisTicketStore.from_url(
        url.get_secret_value(),
        ttl_seconds=settings.realtime_ticket_ttl_seconds,
        namespace=f"{settings.redis_namespace}:tickets",
    )


_TICKET_STORES: dict[str, Callable[[Settings], TicketStore]] = {
    InMemoryTicketStore.name: lambda settings: InMemoryTicketStore(
        ttl_seconds=settings.realtime_ticket_ttl_seconds
    ),
    "redis": _build_redis_tickets,
}

TICKET_STORE_NAMES: tuple[str, ...] = tuple(sorted(_TICKET_STORES))


def build_ticket_store(settings: Settings) -> TicketStore:
    """Construct the ticket store named by settings."""
    return _TICKET_STORES[settings.realtime_ticket_store](settings)


@lru_cache(maxsize=1)
def _default_ticket_store() -> TicketStore:
    return build_ticket_store(get_settings())


def get_ticket_store() -> TicketStore:
    """The process-wide ticket store.

    Cached because the in-memory store *is* its own storage: a fresh one per
    request would issue tickets nothing could redeem.
    """
    return _default_ticket_store()
