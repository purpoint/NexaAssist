"""Nothing sensitive reaches a log line or an error body.

Every fixture below is a deliberate, synthetic secret or piece of customer
content, injected at a boundary where a careless implementation would carry it
onward. Each existing redaction rule is exercised rather than trusted, and the
correlation stamp is checked to be an identifier and nothing more.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.intent import get_llm_provider
from app.core.config import Settings
from app.core.logging import (
    REDACTED,
    SecretRedactingFilter,
    TraceContextFilter,
    configure_logging,
)
from app.llm.base import LLMConfig
from app.llm.providers.static_provider import StaticLLMProvider
from app.main import create_app
from app.observability.spans import OMITTED, SpanKind, sanitise_attributes
from app.observability.tracer import InMemoryRecorder, Tracer, current_trace_id

# Synthetic throughout. These exist to prove redaction, and are not real.
FAKE_GROQ_KEY = "gsk_0123456789abcdefghijklmnop"  # secret-scan: synthetic
FAKE_BEARER = "Bearer abcdef0123456789.token.value"
FAKE_DB_URL = "postgresql+asyncpg://appuser:hunter2@db.internal:5432/nexaassist"
FAKE_REDIS_URL = "redis://default:swordfish@cache.internal:6379/0"
FAKE_API_KEY = "web-app-key-0123456789abcdef"

CUSTOMER_MESSAGE = "my card 4111111111111111 was charged twice"
PROMPT_TEXT = "You are a support assistant. Answer only from the sources."
ANSWER_TEXT = "I can see the duplicate charge on your account."


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    return SecretRedactingFilter(secrets).redact(text)


# --------------------------------------------------------------------------
# The existing redaction rules still hold


@pytest.mark.parametrize(
    "carrier",
    [
        FAKE_GROQ_KEY,
        f"authorization: {FAKE_GROQ_KEY}",
        f"connecting with {FAKE_GROQ_KEY} failed",
    ],
)
def test_provider_keys_are_scrubbed(carrier: str) -> None:
    assert FAKE_GROQ_KEY not in redact(carrier)
    assert REDACTED in redact(carrier)


def test_bearer_tokens_are_scrubbed() -> None:
    assert "abcdef0123456789" not in redact(f"sent {FAKE_BEARER}")


@pytest.mark.parametrize("url", [FAKE_DB_URL, FAKE_REDIS_URL])
def test_connection_string_passwords_are_scrubbed(url: str) -> None:
    scrubbed = redact(f"could not connect to {url}")
    assert "hunter2" not in scrubbed
    assert "swordfish" not in scrubbed


def test_api_key_assignments_are_scrubbed() -> None:
    for line in ("GROQ_API_KEY=abc123def456", "x-api-key: abc123def456"):
        assert "abc123def456" not in redact(line)


def test_a_registered_literal_is_scrubbed_even_without_a_shape() -> None:
    """A shared key looks like nothing in particular, so it is registered."""
    assert FAKE_API_KEY not in redact(f"header was {FAKE_API_KEY}", (FAKE_API_KEY,))


def test_redaction_survives_deferred_formatting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A secret passed as %s never appears in record.msg."""
    logger = logging.getLogger("app.test.redaction")
    handler_filter = SecretRedactingFilter()
    record = logger.makeRecord(
        "app.test.redaction", logging.WARNING, __file__, 1,
        "connecting with %s", (FAKE_GROQ_KEY,), None,
    )
    handler_filter.filter(record)
    assert FAKE_GROQ_KEY not in record.getMessage()


# --------------------------------------------------------------------------
# The application registers its own secrets


def test_every_configured_secret_is_registered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A traceback from a third-party library is scrubbed too."""
    settings = Settings(
        llm_api_key=FAKE_GROQ_KEY,
        database_url="postgresql+asyncpg://appuser:hunter2@db.internal:5432/x",
        redis_url=FAKE_REDIS_URL,
        auth_provider="api_key",
        auth_api_keys=f"web-app:{FAKE_API_KEY}",
    )
    create_app(settings)
    # create_app reconfigures logging and replaces the root handlers, removing
    # the one caplog installed. Without re-attaching it, caplog.records is
    # empty and every assertion below passes vacuously.
    logging.getLogger().addHandler(caplog.handler)
    caplog.set_level(logging.WARNING)
    try:
        logging.getLogger("third.party").warning(
            "boom %s %s %s", FAKE_GROQ_KEY, FAKE_REDIS_URL, FAKE_API_KEY
        )
        emitted = [record.getMessage() for record in caplog.records]
        assert emitted, "the log line was actually captured"
        rendered = " ".join(emitted)
        assert FAKE_GROQ_KEY not in rendered
        assert "swordfish" not in rendered
        assert FAKE_API_KEY not in rendered
    finally:
        configure_logging()


# --------------------------------------------------------------------------
# Trace correlation


def test_every_record_carries_a_trace_id() -> None:
    """A format string that sometimes lacks a field fails when it matters."""
    record = logging.getLogger("app.test.trace").makeRecord(
        "app.test.trace", logging.INFO, __file__, 1, "hello", (), None
    )
    TraceContextFilter().filter(record)
    assert record.trace_id == "-"


def test_a_record_inside_a_span_carries_that_trace() -> None:
    tracer = Tracer(InMemoryRecorder())
    with tracer.span("work", SpanKind.REQUEST):
        expected = current_trace_id()
        record = logging.getLogger("app.test.trace").makeRecord(
            "app.test.trace", logging.INFO, __file__, 1, "hello", (), None
        )
        TraceContextFilter().filter(record)
    assert record.trace_id == expected and expected is not None


def test_a_trace_id_is_an_identifier_not_content() -> None:
    tracer = Tracer(InMemoryRecorder())
    with tracer.span("work", SpanKind.REQUEST):
        trace = current_trace_id()
    assert trace and all(c in "0123456789abcdef" for c in trace)


# --------------------------------------------------------------------------
# Customer and prompt content


@pytest.mark.parametrize("content", [CUSTOMER_MESSAGE, PROMPT_TEXT, ANSWER_TEXT])
def test_content_can_never_become_a_span_attribute(content: str) -> None:
    assert sanitise_attributes({"value": content}) == {"value": OMITTED}


def test_a_full_request_logs_no_content(caplog: pytest.LogCaptureFixture) -> None:
    """The end-to-end check: one request, everything captured, nothing leaked."""
    from app.api.v1.assistant import get_assistant_service
    from app.routing.router import RouteReason
    from app.schemas.intent import IntentCategory
    from app.services.assistant import AssistantReply

    class Stub:
        async def respond(self, message: str, **kwargs: object) -> AssistantReply:
            return AssistantReply(
                reply=ANSWER_TEXT,
                intent=IntentCategory.BILLING,
                confidence=0.9,
                handler="agent",
                route_reason=RouteReason.MATCHED,
                fallback=False,
                handled=True,
            )

    app = create_app()
    app.dependency_overrides[get_assistant_service] = Stub

    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/assistant/messages", json={"message": CUSTOMER_MESSAGE}
            )

    assert response.status_code == 200
    for leak in ("4111111111111111", "charged twice", ANSWER_TEXT):
        assert leak not in caplog.text


# --------------------------------------------------------------------------
# Client-facing errors stay sanitized while staying useful internally


def test_an_error_body_never_carries_a_cause(caplog: pytest.LogCaptureFixture) -> None:
    from app.api.v1.assistant import get_assistant_service
    from app.llm.errors import LLMUnavailableError

    class Failing:
        async def respond(self, message: str, **kwargs: object) -> object:
            raise LLMUnavailableError()

    app = create_app()
    app.dependency_overrides[get_assistant_service] = Failing
    # create_app reconfigures logging, which replaces the root handlers and
    # removes the one caplog installed before the test body ran.
    logging.getLogger().addHandler(caplog.handler)
    app.dependency_overrides[get_assistant_service] = Failing

    with caplog.at_level(logging.WARNING):
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/assistant/messages", json={"message": CUSTOMER_MESSAGE}
            )

    assert response.status_code == 503
    body = response.text
    assert "Traceback" not in body
    assert "postgresql" not in body and "gsk_" not in body
    assert CUSTOMER_MESSAGE not in body
    # Still useful internally: the category is logged even though the body is bare.
    assert "llm_unavailable" in caplog.text


def _app_that_can_reach_validation() -> FastAPI:
    """An app whose LLM dependency resolves, so the body is what fails.

    FastAPI resolves dependencies before it validates a body. On a machine
    with no provider key -- a fresh clone, a CI runner -- building the real
    provider raises first, and these tests got a 500 about configuration
    instead of the 422 they are about. Substituting a deterministic provider
    puts validation back on the path being tested.
    """
    app = create_app()
    # A factory, not the class: FastAPI reads a class dependency's
    # __init__ signature and turns its parameters into request fields, so
    # passing the class itself adds `config` to the very error body this
    # test is asserting about.
    app.dependency_overrides[get_llm_provider] = lambda: StaticLLMProvider(
        LLMConfig(provider="static", model="static-model")
    )
    return app


def test_a_validation_error_does_not_echo_the_input() -> None:
    """The default FastAPI body embeds the offending value; ours does not."""
    with TestClient(_app_that_can_reach_validation()) as client:
        response = client.post(
            "/api/v1/intent/analyze", json={"message": "", "extra": CUSTOMER_MESSAGE}
        )
    assert response.status_code == 422
    body = response.text
    assert "4111111111111111" not in body
    assert CUSTOMER_MESSAGE not in body


def test_a_validation_error_still_says_which_fields_were_wrong() -> None:
    """Sanitised, not useless: a client must be able to fix its request."""
    with TestClient(_app_that_can_reach_validation()) as client:
        body = client.post(
            "/api/v1/intent/analyze", json={"message": "", "extra": 1}
        ).json()
    assert body["code"] == "invalid_request"
    assert set(body["details"]["fields"]) == {"message", "extra"}


def test_a_validation_error_uses_the_one_error_shape() -> None:
    from app.schemas.common import ErrorResponse

    with TestClient(create_app()) as client:
        body = client.post("/api/v1/intent/analyze", json={}).json()
    assert set(body) <= set(ErrorResponse.model_fields)
