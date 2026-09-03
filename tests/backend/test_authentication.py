"""Authentication: identity, credentials, and what a refusal reveals."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.api.v1.assistant import get_assistant_service
from app.api.v1.conversations import get_conversation_service, get_customer_service
from app.api.v1.identity import API_KEY_HEADER, require_identity
from app.auth.base import Authenticator
from app.auth.errors import (
    AuthenticationConfigurationError,
    AuthenticationError,
    AuthenticationRequiredError,
    InvalidCredentialsError,
)
from app.auth.factory import AUTHENTICATOR_NAMES, build_authenticator, get_authenticator
from app.auth.identity import ANONYMOUS_SUBJECT, IdentityKind, RequestIdentity
from app.auth.providers import (
    MIN_KEY_LENGTH,
    AnonymousAuthenticator,
    ApiKeyAuthenticator,
    fingerprint,
    parse_credentials,
)
from app.core.config import Settings
from app.main import create_app
from app.routing.router import RouteReason
from app.schemas.intent import IntentCategory
from app.services.assistant import AssistantReply

KEY = "0123456789abcdef-not-a-real-key"
OTHER_KEY = "fedcba9876543210-also-not-real"
SUBJECT = "web-app"
ENTRIES = f"{SUBJECT}:{KEY},worker:{OTHER_KEY}"

ASSISTANT = "/api/v1/assistant/messages"
CONVERSATIONS = "/api/v1/conversations"


def key_authenticator(entries: str = ENTRIES) -> ApiKeyAuthenticator:
    return build_authenticator(
        Settings(auth_provider="api_key", auth_api_keys=entries)
    )


# --------------------------------------------------------------------------
# The identity value


def test_an_unauthenticated_request_still_has_an_identity() -> None:
    """One type for both cases: `| None` is what gets forgotten."""
    identity = RequestIdentity.anonymous()
    assert identity.authenticated is False
    assert identity.subject == ANONYMOUS_SUBJECT
    assert identity.kind is IdentityKind.ANONYMOUS


def test_an_authenticated_identity_carries_its_subject() -> None:
    identity = RequestIdentity.api_key(SUBJECT)
    assert identity.authenticated is True
    assert identity.subject == SUBJECT
    assert identity.kind is IdentityKind.API_KEY


def test_an_identity_is_frozen() -> None:
    with pytest.raises(Exception):
        RequestIdentity.anonymous().subject = "someone-else"  # type: ignore[misc]


def test_an_identity_never_carries_the_credential() -> None:
    assert KEY not in RequestIdentity.api_key(SUBJECT).model_dump_json()


# --------------------------------------------------------------------------
# The authenticators


def test_both_authenticators_satisfy_the_protocol() -> None:
    assert isinstance(AnonymousAuthenticator(), Authenticator)
    assert isinstance(key_authenticator(), Authenticator)


def test_the_registry_matches_the_setting() -> None:
    allowed = Settings.model_fields["auth_provider"].annotation
    assert set(AUTHENTICATOR_NAMES) == set(allowed.__args__)


def test_the_default_is_not_to_authenticate() -> None:
    """So a deployment that has not opted in behaves exactly as before."""
    built = build_authenticator(Settings())
    assert isinstance(built, AnonymousAuthenticator)
    assert built.protects is False


@pytest.mark.anyio
async def test_the_anonymous_authenticator_accepts_everything() -> None:
    authenticator = AnonymousAuthenticator()
    assert (await authenticator.authenticate(None)).authenticated is False
    # A key sent to a deployment that does not check them is ignored, not
    # rejected: refusing it would break a harmlessly over-eager client.
    assert (await authenticator.authenticate("anything")).authenticated is False


@pytest.mark.anyio
async def test_a_valid_key_yields_its_subject() -> None:
    identity = await key_authenticator().authenticate(KEY)
    assert identity.subject == SUBJECT
    assert identity.authenticated is True


@pytest.mark.anyio
async def test_each_configured_key_maps_to_its_own_subject() -> None:
    authenticator = key_authenticator()
    assert (await authenticator.authenticate(OTHER_KEY)).subject == "worker"


@pytest.mark.anyio
async def test_missing_credentials_are_refused() -> None:
    with pytest.raises(AuthenticationRequiredError):
        await key_authenticator().authenticate(None)
    with pytest.raises(AuthenticationRequiredError):
        await key_authenticator().authenticate("")


@pytest.mark.anyio
async def test_wrong_credentials_are_refused() -> None:
    with pytest.raises(InvalidCredentialsError):
        await key_authenticator().authenticate("wrong-but-long-enough-key")


@pytest.mark.anyio
async def test_a_prefix_of_a_valid_key_is_refused() -> None:
    with pytest.raises(InvalidCredentialsError):
        await key_authenticator().authenticate(KEY[:-1])


def test_both_refusals_look_the_same_to_a_client() -> None:
    """Telling "wrong key" from "no key" teaches an attacker the shape."""
    missing = AuthenticationRequiredError().to_response()
    invalid = InvalidCredentialsError().to_response()
    assert missing.message == invalid.message
    assert AuthenticationRequiredError.status_code == InvalidCredentialsError.status_code


def test_a_refusal_says_how_to_authenticate() -> None:
    assert AuthenticationError().headers["WWW-Authenticate"] == API_KEY_HEADER


# --------------------------------------------------------------------------
# Configuration


def test_entries_parse_into_subjects_and_secrets() -> None:
    parsed = parse_credentials([f"{SUBJECT}:{KEY}"])
    assert parsed[0].subject == SUBJECT
    assert parsed[0].secret.get_secret_value() == KEY


def test_a_secret_may_contain_a_colon() -> None:
    """Split once from the left, so a key with separators survives."""
    parsed = parse_credentials([f"{SUBJECT}:abc:def:0123456789abcdef"])
    assert parsed[0].secret.get_secret_value() == "abc:def:0123456789abcdef"


def test_blank_entries_are_ignored() -> None:
    assert parse_credentials(["", "   "]) == []


@pytest.mark.parametrize(
    "entry", ["nocolon", ":secretsecretsecret", f"{SUBJECT}:", f"{SUBJECT}:short"]
)
def test_a_malformed_entry_is_rejected_at_construction(entry: str) -> None:
    with pytest.raises(AuthenticationConfigurationError):
        parse_credentials([entry])


def test_a_short_key_is_rejected() -> None:
    with pytest.raises(AuthenticationConfigurationError):
        parse_credentials([f"{SUBJECT}:{'a' * (MIN_KEY_LENGTH - 1)}"])


def test_a_duplicate_subject_is_rejected() -> None:
    with pytest.raises(AuthenticationConfigurationError):
        ApiKeyAuthenticator(parse_credentials([f"a:{KEY}", f"a:{OTHER_KEY}"]))


def test_no_keys_at_all_is_rejected() -> None:
    with pytest.raises(AuthenticationConfigurationError):
        ApiKeyAuthenticator([])


def test_selecting_key_auth_without_keys_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="AUTH_API_KEYS"):
        Settings(auth_provider="api_key")


def test_a_configuration_error_never_quotes_the_entry() -> None:
    with pytest.raises(AuthenticationConfigurationError) as caught:
        parse_credentials([f"{SUBJECT}:short"])
    assert "short" not in caught.value.to_response().model_dump_json()


# --------------------------------------------------------------------------
# Nothing leaks


def test_the_authenticator_never_exposes_its_keys() -> None:
    authenticator = key_authenticator()
    assert authenticator.subjects == (SUBJECT, "worker")
    assert KEY not in repr(authenticator.subjects)


@pytest.mark.anyio
async def test_a_failed_attempt_logs_a_fingerprint_not_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.auth.providers"):
        with pytest.raises(InvalidCredentialsError):
            await key_authenticator().authenticate("wrong-but-long-enough-key")
    assert "wrong-but-long-enough-key" not in caplog.text
    assert fingerprint("wrong-but-long-enough-key") in caplog.text


@pytest.mark.anyio
async def test_a_successful_attempt_logs_the_subject_not_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.auth.providers"):
        await key_authenticator().authenticate(KEY)
    assert f"subject={SUBJECT}" in caplog.text
    assert KEY not in caplog.text


def test_a_fingerprint_is_short_and_not_the_key() -> None:
    made = fingerprint(KEY)
    assert made != KEY and len(made) == 12
    assert fingerprint(KEY) == fingerprint(KEY)
    assert fingerprint(KEY) != fingerprint(OTHER_KEY)


# --------------------------------------------------------------------------
# Over HTTP


class StubAssistant:
    """Answers without a database, so the happy path is genuinely reachable."""

    async def respond(self, message: str, *, conversation_id=None) -> AssistantReply:
        return AssistantReply(
            reply="a reply",
            intent=IntentCategory.OTHER,
            confidence=0.0,
            handler="fallback",
            route_reason=RouteReason.NO_CATEGORY,
            fallback=True,
            handled=False,
        )


def protected_client(entries: str | None = ENTRIES) -> TestClient:
    """An app with authentication on, and the DB-backed services stubbed.

    The assistant stub is a working one rather than a bare object: a stub that
    raises would make TestClient re-raise, and the assertion "authentication
    was not the blocker" would never actually run.
    """
    settings = (
        Settings(auth_provider="api_key", auth_api_keys=entries)
        if entries is not None
        else Settings()
    )
    app = create_app(settings)
    app.dependency_overrides[get_authenticator] = lambda: build_authenticator(settings)
    app.dependency_overrides[get_assistant_service] = StubAssistant
    for dependency in (get_conversation_service, get_customer_service):
        app.dependency_overrides[dependency] = lambda: object()
    return TestClient(app)


def test_a_protected_route_refuses_a_request_with_no_key() -> None:
    with protected_client() as client:
        response = client.post(ASSISTANT, json={"message": "hello"})
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "authentication_required"
    assert response.headers["www-authenticate"] == API_KEY_HEADER


def test_a_protected_route_refuses_a_wrong_key() -> None:
    with protected_client() as client:
        response = client.post(
            ASSISTANT,
            json={"message": "hello"},
            headers={API_KEY_HEADER: "wrong-but-long-enough"},
        )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_a_refusal_never_echoes_what_was_presented() -> None:
    with protected_client() as client:
        body = client.post(
            ASSISTANT,
            json={"message": "hello"},
            headers={API_KEY_HEADER: "leak-me-please-0123456789"},
        ).text
    assert "leak-me-please" not in body


def test_a_valid_key_is_answered() -> None:
    with protected_client() as client:
        response = client.post(
            ASSISTANT, json={"message": "hello"}, headers={API_KEY_HEADER: KEY}
        )
    assert response.status_code == 200
    assert response.json()["reply"] == "a reply"


def test_the_conversation_routes_are_protected() -> None:
    with protected_client() as client:
        assert client.post(CONVERSATIONS, json={"customer_email": "a@b.co"}).status_code == 401
        assert client.get(f"{CONVERSATIONS}/{'0' * 8}-0000-0000-0000-000000000000").status_code == 401


def test_health_and_readiness_stay_open() -> None:
    """Liveness must answer without a credential, or nothing can probe it."""
    with protected_client() as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/v1/ready").status_code in (200, 503)
        assert client.get("/openapi.json").status_code == 200


def test_with_authentication_disabled_no_key_is_needed() -> None:
    """The pre-M19 contract, still intact."""
    with protected_client(entries=None) as client:
        response = client.post(ASSISTANT, json={"message": "hello"})
    assert response.status_code == 200


def test_a_key_sent_to_an_open_deployment_is_ignored() -> None:
    with protected_client(entries=None) as client:
        response = client.post(
            ASSISTANT, json={"message": "hello"}, headers={API_KEY_HEADER: "whatever"}
        )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_the_dependency_propagates_the_identity() -> None:
    identity = await require_identity(KEY, key_authenticator())
    assert identity.subject == SUBJECT and identity.authenticated is True


@pytest.mark.anyio
async def test_the_dependency_yields_anonymous_when_auth_is_off() -> None:
    identity = await require_identity(None, AnonymousAuthenticator())
    assert identity.authenticated is False
