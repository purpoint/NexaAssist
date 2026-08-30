"""Intent analysis: schema, service, and endpoint.

No test reaches the network. Service and API tests run against a double
injected through ``dependency_overrides``; the one end-to-end test uses the
shipped ``StaticLLMProvider``.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.intent import get_intent_service
from app.core.config import Settings
from app.llm.base import LLMConfig
from app.llm.errors import LLMError
from app.llm.factory import build_provider, get_llm_provider
from app.llm.prompts import INTENT_SYSTEM_PROMPT
from app.main import create_app
from app.schemas.intent import (
    STATIC_EXAMPLE,
    IntentAnalysis,
    IntentAnalysisRequest,
    IntentCategory,
)
from app.services.intent import IntentService
from tests.backend.llm.fakes import FakeLLMProvider

ANALYZE_URL = "/api/v1/intent/analyze"

SAMPLE = IntentAnalysis(
    intent=IntentCategory.BILLING,
    confidence=0.93,
    reason="Reports a duplicate charge on an invoice.",
)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_intent_categories_are_exactly_the_six_approved_values() -> None:
    """Guards against a category being added or renamed unnoticed."""
    assert [category.value for category in IntentCategory] == [
        "billing",
        "technical_support",
        "account",
        "product_question",
        "complaint",
        "other",
    ]


def test_valid_intent_analysis() -> None:
    analysis = IntentAnalysis(
        intent=IntentCategory.ACCOUNT, confidence=0.5, reason="Sign-in problem."
    )

    assert analysis.intent is IntentCategory.ACCOUNT
    assert analysis.model_dump()["intent"] == "account"


def test_intent_analysis_rejects_an_unknown_category() -> None:
    with pytest.raises(ValidationError):
        IntentAnalysis(intent="refund", confidence=0.5, reason="Nope.")


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_intent_analysis_rejects_out_of_range_confidence(confidence: float) -> None:
    with pytest.raises(ValidationError):
        IntentAnalysis(
            intent=IntentCategory.OTHER, confidence=confidence, reason="Out of range."
        )


def test_intent_analysis_rejects_a_non_numeric_confidence() -> None:
    with pytest.raises(ValidationError):
        IntentAnalysis(intent=IntentCategory.OTHER, confidence="high", reason="Nope.")


def test_intent_analysis_rejects_an_empty_reason() -> None:
    with pytest.raises(ValidationError):
        IntentAnalysis(intent=IntentCategory.OTHER, confidence=0.5, reason="")


def test_intent_analysis_rejects_an_overlong_reason() -> None:
    with pytest.raises(ValidationError):
        IntentAnalysis(
            intent=IntentCategory.OTHER, confidence=0.5, reason="x" * 281
        )


def test_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        IntentAnalysisRequest(message="hello", urgency="high")


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_service_calls_the_provider_once_with_the_right_prompt() -> None:
    provider = FakeLLMProvider(output=SAMPLE)

    await IntentService(provider).analyze("I was charged twice for March")

    assert provider.call_count == 1
    prompt, schema = provider.calls[0]
    assert prompt.system == INTENT_SYSTEM_PROMPT
    assert prompt.user == "I was charged twice for March"
    assert schema is IntentAnalysis


@pytest.mark.anyio
async def test_service_returns_the_provider_output_unchanged() -> None:
    provider = FakeLLMProvider(output=SAMPLE)

    result = await IntentService(provider).analyze("anything")

    assert result == SAMPLE


@pytest.mark.anyio
async def test_service_does_not_swallow_a_provider_error() -> None:
    """Recovery belongs to the hardening step; hiding an outage never does."""
    provider = FakeLLMProvider(error=LLMError())

    with pytest.raises(LLMError):
        await IntentService(provider).analyze("anything")


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------


def client_with(provider: object, settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_llm_provider] = lambda: provider
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_provider() -> FakeLLMProvider:
    return FakeLLMProvider(output=SAMPLE)


@pytest.fixture
def intent_client(
    fake_provider: FakeLLMProvider, settings: Settings
) -> Iterator[TestClient]:
    yield from client_with(fake_provider, settings)


def test_analyze_returns_the_classification(intent_client: TestClient) -> None:
    response = intent_client.post(ANALYZE_URL, json={"message": "I was charged twice"})

    assert response.status_code == 200
    assert response.json() == {
        "intent": "billing",
        "confidence": 0.93,
        "reason": "Reports a duplicate charge on an invoice.",
    }


def test_route_delegates_to_the_service(
    intent_client: TestClient, fake_provider: FakeLLMProvider
) -> None:
    """The route holds no logic: one message in, one provider call out."""
    intent_client.post(ANALYZE_URL, json={"message": "hello there"})

    assert fake_provider.call_count == 1
    assert fake_provider.calls[0][0].user == "hello there"


@pytest.mark.parametrize(
    ("body", "case"),
    [
        ({}, "missing message"),
        ({"message": ""}, "empty message"),
        ({"message": "x" * 8001}, "message too long"),
        ({"message": "hi", "urgency": "high"}, "unknown field"),
    ],
)
def test_invalid_requests_are_rejected(
    intent_client: TestClient, body: dict[str, object], case: str
) -> None:
    assert intent_client.post(ANALYZE_URL, json=body).status_code == 422, case


def test_provider_failure_surfaces_as_the_shared_error_envelope(
    settings: Settings,
) -> None:
    provider = FakeLLMProvider(error=LLMError())

    for test_client in client_with(provider, settings):
        response = test_client.post(ANALYZE_URL, json={"message": "hi"})

    assert response.status_code == 500
    assert response.json() == {
        "code": "llm_error",
        "message": "The language model request could not be completed.",
    }


def test_static_provider_serves_the_endpoint_without_credentials(
    settings: Settings,
) -> None:
    """`LLM_PROVIDER=static` must work end to end, through the real route."""
    provider = build_provider(LLMConfig(provider="static", model="test-model"))

    for test_client in client_with(provider, settings):
        response = test_client.post(ANALYZE_URL, json={"message": "hello"})

    assert response.status_code == 200
    assert response.json() == STATIC_EXAMPLE.model_dump(mode="json")


def test_service_dependency_can_be_overridden_directly(settings: Settings) -> None:
    """The service seam is injectable too, not just the provider."""
    app = create_app(settings)
    provider = FakeLLMProvider(output=SAMPLE)
    app.dependency_overrides[get_intent_service] = lambda: IntentService(provider)

    with TestClient(app) as test_client:
        assert test_client.post(ANALYZE_URL, json={"message": "hi"}).status_code == 200


# --------------------------------------------------------------------------
# The schema actually sent to Groq
# --------------------------------------------------------------------------


def test_strict_schema_for_intent_analysis_meets_groq_requirements() -> None:
    from app.llm.providers.groq_provider import strict_json_schema

    document = strict_json_schema(IntentAnalysis)

    assert document["additionalProperties"] is False
    assert sorted(document["required"]) == ["confidence", "intent", "reason"]
    assert document["$defs"]["IntentCategory"]["enum"] == [
        "billing",
        "technical_support",
        "account",
        "product_question",
        "complaint",
        "other",
    ]
