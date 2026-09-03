"""Authorization: ownership, and what a refusal must not reveal."""

import pytest

from app.auth.authorization import (
    Authorizer,
    OpenAuthorizer,
    OwnerScope,
    SubjectScopedAuthorizer,
)
from app.auth.factory import AUTHORIZER_NAMES, build_authorizer
from app.auth.identity import RequestIdentity
from app.core.config import Settings

WEB = RequestIdentity.api_key("web-app")
WORKER = RequestIdentity.api_key("worker")
KEYS = "web-app:0123456789abcdef0,worker:fedcba9876543210a"


def scoped() -> Settings:
    return Settings(
        authz_provider="subject", auth_provider="api_key", auth_api_keys=KEYS
    )


# --------------------------------------------------------------------------
# The scope rule


def test_a_scope_permits_its_own_subject() -> None:
    assert OwnerScope(subject="web-app").permits("web-app") is True


def test_a_scope_refuses_another_subject() -> None:
    assert OwnerScope(subject="web-app").permits("worker") is False


def test_a_scope_refuses_an_unowned_resource() -> None:
    """Fail closed.

    Rows created before ownership existed carry no owner. Sharing them with
    every authenticated subject would be worse than losing access to them.
    """
    assert OwnerScope(subject="web-app").permits(None) is False


def test_a_scope_is_frozen() -> None:
    with pytest.raises(Exception):
        OwnerScope(subject="web-app").subject = "worker"  # type: ignore[misc]


# --------------------------------------------------------------------------
# The authorizers


def test_both_authorizers_satisfy_the_protocol() -> None:
    assert isinstance(OpenAuthorizer(), Authorizer)
    assert isinstance(SubjectScopedAuthorizer(), Authorizer)


def test_the_registry_matches_the_setting() -> None:
    allowed = Settings.model_fields["authz_provider"].annotation
    assert set(AUTHORIZER_NAMES) == set(allowed.__args__)


def test_the_default_enforces_nothing() -> None:
    """So every endpoint behaves exactly as it did before M19."""
    built = build_authorizer(Settings())
    assert isinstance(built, OpenAuthorizer)
    assert built.scopes is False
    assert built.scope_for(WEB) is None


def test_the_open_authorizer_stamps_no_owner() -> None:
    """Otherwise enabling scoping later would hand old rows to a subject."""
    assert OpenAuthorizer().owner_for(WEB) is None


def test_the_scoped_authorizer_stamps_and_scopes_by_subject() -> None:
    authorizer = build_authorizer(scoped())
    assert authorizer.scopes is True
    assert authorizer.owner_for(WEB) == "web-app"
    assert authorizer.scope_for(WEB) == OwnerScope(subject="web-app")


def test_one_subjects_scope_does_not_permit_anothers_rows() -> None:
    authorizer = SubjectScopedAuthorizer()
    assert authorizer.scope_for(WEB).permits(authorizer.owner_for(WORKER)) is False
    assert authorizer.scope_for(WEB).permits(authorizer.owner_for(WEB)) is True


def test_an_anonymous_identity_scopes_to_anonymous() -> None:
    """Which is why the configuration refuses this combination outright."""
    assert SubjectScopedAuthorizer().owner_for(RequestIdentity.anonymous()) == "anonymous"


# --------------------------------------------------------------------------
# Configuration


def test_scoping_without_authentication_is_refused() -> None:
    """It would restrict everything to the single subject "anonymous"."""
    with pytest.raises(ValueError, match="AUTH_PROVIDER"):
        Settings(authz_provider="subject")


def test_scoping_with_authentication_is_accepted() -> None:
    assert scoped().authz_provider == "subject"


def test_an_unknown_authorizer_is_rejected() -> None:
    with pytest.raises(Exception):
        Settings(authz_provider="tenant")
