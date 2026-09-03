"""Logging: credentials are redacted, and content is never logged at all."""

import logging

import pytest

from app.core.logging import REDACTED, SecretRedactingFilter, configure_logging
from app.llm.base import LLMPrompt, LLMUsage, StructuredCompletion
from app.schemas.intent import IntentAnalysis, IntentCategory
from app.services.intent import IntentService
from tests.backend.llm.fakes import FakeLLMProvider

# Not a key: a sequence shaped like one, so the redaction under test has
# something to redact.
FAKE_KEY = "gsk_0123456789abcdefghijKLMNOPqrstuvwxyz0123456789ab"  # secret-scan: synthetic
CONFIGURED = "a-configured-secret-value-not-key-shaped"


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    return SecretRedactingFilter(secrets).redact(text)


# --------------------------------------------------------------------------
# Pattern-based redaction
# --------------------------------------------------------------------------


def test_groq_style_key_is_redacted() -> None:
    result = redact(f"client built with api key {FAKE_KEY} ok")

    assert FAKE_KEY not in result
    assert REDACTED in result


def test_bearer_token_is_redacted() -> None:
    result = redact("headers: Bearer abcdef0123456789xyz")

    assert "abcdef0123456789xyz" not in result
    assert "Bearer" in result and REDACTED in result


@pytest.mark.parametrize(
    "line",
    [
        "authorization: sometokenvalue123",
        "x-api-key=sometokenvalue123",
        "GROQ_API_KEY=sometokenvalue123",
    ],
)
def test_credential_headers_and_assignments_are_redacted(line: str) -> None:
    result = redact(line)

    assert "sometokenvalue123" not in result
    assert REDACTED in result


def test_configured_secret_literal_is_redacted() -> None:
    """Covers a secret that matches no pattern but was registered explicitly."""
    result = redact(f"boom: {CONFIGURED}", secrets=(CONFIGURED,))

    assert CONFIGURED not in result
    assert REDACTED in result


def test_ordinary_text_is_left_alone() -> None:
    line = "llm call failed provider=groq model=openai/gpt-oss-120b error_category=llm_timeout"

    assert redact(line) == line


# --------------------------------------------------------------------------
# Redaction through the real logging pipeline
# --------------------------------------------------------------------------


def test_secret_passed_as_a_format_argument_is_redacted() -> None:
    """The secret never appears in record.msg, only in the rendered message."""
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="key is %s", args=(FAKE_KEY,), exc_info=None,
    )

    SecretRedactingFilter().filter(record)

    assert FAKE_KEY not in record.getMessage()
    assert REDACTED in record.getMessage()


def test_configured_secret_is_redacted_end_to_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SecretRedactingFilter((CONFIGURED,)))
    logger = logging.getLogger("test.redaction")
    logger.addHandler(handler)
    try:
        with caplog.at_level(logging.INFO, logger="test.redaction"):
            logger.info("value=%s", CONFIGURED)
    finally:
        logger.removeHandler(handler)

    assert CONFIGURED not in caplog.text


def test_configure_logging_installs_the_filter() -> None:
    configure_logging("INFO", secrets=(CONFIGURED,))

    handlers = logging.getLogger().handlers
    assert any(
        isinstance(f, SecretRedactingFilter) for h in handlers for f in h.filters
    )
    configure_logging("INFO")  # restore the default configuration


# --------------------------------------------------------------------------
# Content policy: what the service is allowed to log
# --------------------------------------------------------------------------


MESSAGE = "My card ending 4242 was charged twice, call me on 555-0100"
ANALYSIS = IntentAnalysis(
    intent=IntentCategory.BILLING,
    confidence=0.9,
    reason="Customer reports a duplicate charge on card 4242.",
)


@pytest.mark.anyio
async def test_service_logs_metadata_but_never_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = FakeLLMProvider(output=ANALYSIS)

    with caplog.at_level(logging.INFO, logger="app.services.intent"):
        await IntentService(provider).analyze(MESSAGE)

    text = caplog.text
    # Content: absent.
    assert MESSAGE not in text
    assert "4242" not in text
    assert ANALYSIS.reason not in text
    # Metadata: present.
    assert "provider=fake" in text
    assert "model=fake-model" in text
    assert "prompt_version=intent/v1" in text
    assert "intent=billing" in text
    assert "tokens_in=11" in text
    assert "tokens_out=22" in text


@pytest.mark.anyio
async def test_prompt_text_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    provider = FakeLLMProvider(output=ANALYSIS)

    with caplog.at_level(logging.DEBUG):
        await IntentService(provider).analyze(MESSAGE)

    assert "You classify a single customer support message" not in caplog.text


def test_structured_completion_carries_usage_without_content_in_logs() -> None:
    """Guards the shape the service logs from."""
    completion = StructuredCompletion[IntentAnalysis](
        output=ANALYSIS, provider="fake", model="m", usage=LLMUsage(input_tokens=1)
    )

    assert completion.usage.input_tokens == 1
    assert isinstance(LLMPrompt(system="s", user="u").user, str)
