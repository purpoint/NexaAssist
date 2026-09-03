"""The assistant service and its endpoint, offline.

The pipeline's own pieces are already covered by M8, M10, and M11. What is
under test here is the composition -- the order the steps run in, and what the
API publishes about them.
"""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.v1.assistant import get_assistant_service
from app.escalation.criteria import EscalationReason
from app.escalation.handoff import HandoffResult
from app.llm.errors import LLMUnavailableError
from app.main import create_app
from app.routing.router import RouteReason, RoutedReply, RoutingDecision
from app.schemas.assistant import MAX_MESSAGE_LENGTH, AssistantMessageResponse
from app.schemas.intent import IntentAnalysis, IntentCategory
from app.services.assistant import AssistantReply, AssistantService

PATH = "/api/v1/assistant/messages"

ANALYSIS = IntentAnalysis(
    intent=IntentCategory.BILLING, confidence=0.9, reason="fixture"
)


class StubIntent:
    def __init__(self, analysis: IntentAnalysis = ANALYSIS, error: Exception | None = None):
        self._analysis = analysis
        self._error = error
        self.seen: list[str] = []

    async def analyze(self, message: str) -> IntentAnalysis:
        self.seen.append(message)
        if self._error:
            raise self._error
        return self._analysis


class StubRouter:
    def __init__(self, routed: RoutedReply) -> None:
        self._routed = routed
        self.calls: list[tuple[str, IntentAnalysis]] = []

    async def route(self, message: str, analysis: IntentAnalysis) -> RoutedReply:
        self.calls.append((message, analysis))
        return self._routed


class StubHandoff:
    def __init__(self, result: HandoffResult) -> None:
        self._result = result
        self.seen: list[RoutedReply] = []

    async def consider(self, message: str, routed: RoutedReply, **kwargs: object):
        self.seen.append(routed)
        return self._result


def routed(
    reply: str = "Here is what I found.",
    *,
    handled: bool = True,
    fallback: bool = False,
    policy_rule: str | None = None,
    policy_modified: bool = False,
) -> RoutedReply:
    return RoutedReply(
        decision=RoutingDecision(
            intent=IntentCategory.BILLING,
            confidence=0.9,
            handler="agent",
            reason=RouteReason.LOW_CONFIDENCE if fallback else RouteReason.MATCHED,
            fallback=fallback,
        ),
        reply=reply,
        handled=handled,
        policy_rule=policy_rule,
        policy_modified=policy_modified,
    )


def service_of(
    routed_reply: RoutedReply | None = None,
    handoff: HandoffResult | None = None,
    intent: StubIntent | None = None,
) -> AssistantService:
    routed_reply = routed_reply or routed()
    return AssistantService(
        intent or StubIntent(),
        StubRouter(routed_reply),
        StubHandoff(handoff or HandoffResult(reply=routed_reply.reply, escalated=False)),
    )


def client_with(service: AssistantService) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_assistant_service] = lambda: service
    return TestClient(app)


# --------------------------------------------------------------------------
# The service composes the pipeline in one order


@pytest.mark.anyio
async def test_the_reply_is_the_one_escalation_approved() -> None:
    """Escalation runs last and may append to the reply."""
    service = service_of(
        routed("Original."),
        HandoffResult(
            reply="Original. A support agent will follow up with you.",
            escalated=True,
            reasons=[EscalationReason.LOW_CONFIDENCE],
        ),
    )
    reply = await service.respond("why?")
    assert reply.reply.endswith("follow up with you.")
    assert reply.escalated is True
    assert reply.escalation_reasons == [EscalationReason.LOW_CONFIDENCE]


@pytest.mark.anyio
async def test_escalation_sees_the_reply_policy_approved() -> None:
    """Escalating on the handler's draft would review something never sent."""
    routed_reply = routed("Policy-approved text.", policy_modified=True, policy_rule="r")
    handoff = StubHandoff(HandoffResult(reply="Policy-approved text.", escalated=False))
    service = AssistantService(StubIntent(), StubRouter(routed_reply), handoff)
    await service.respond("why?")
    assert handoff.seen[0].reply == "Policy-approved text."
    assert handoff.seen[0].policy_modified is True


@pytest.mark.anyio
async def test_the_message_reaches_the_classifier_unchanged() -> None:
    intent = StubIntent()
    await service_of(intent=intent).respond("I was charged twice")
    assert intent.seen == ["I was charged twice"]


@pytest.mark.anyio
async def test_the_decision_trail_is_carried_through() -> None:
    reply = await service_of(routed(handled=False, fallback=True)).respond("why?")
    assert reply.handled is False
    assert reply.fallback is True
    assert reply.route_reason is RouteReason.LOW_CONFIDENCE
    assert reply.intent is IntentCategory.BILLING


@pytest.mark.anyio
async def test_a_provider_failure_propagates_as_an_app_error() -> None:
    """The pipeline does not invent a reply when it could not think."""
    with pytest.raises(LLMUnavailableError):
        await service_of(intent=StubIntent(error=LLMUnavailableError())).respond("hi")


@pytest.mark.anyio
async def test_logs_record_categories_not_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.assistant"):
        await service_of(routed("Your refund is on its way.")).respond(
            "where is my money?"
        )
    assert "intent=billing" in caplog.text
    assert "handler=agent" in caplog.text
    assert "refund is on its way" not in caplog.text
    assert "where is my money" not in caplog.text


# --------------------------------------------------------------------------
# The endpoint


def test_a_message_is_answered() -> None:
    with client_with(service_of(routed("Here is what I found."))) as client:
        response = client.post(PATH, json={"message": "I was charged twice"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Here is what I found."
    assert body["intent"] == "billing"
    assert body["handler"] == "agent"
    assert body["escalated"] is False


def test_the_response_carries_a_trace_id() -> None:
    """Safe to quote in a support ticket: it identifies the request, not its content."""
    with client_with(service_of()) as client:
        body = client.post(PATH, json={"message": "hello"}).json()
    assert body["trace_id"] and len(body["trace_id"]) == 16


def test_an_escalation_is_reported_with_its_review_id() -> None:
    review = uuid.uuid4()
    service = service_of(
        handoff=HandoffResult(
            reply="We will look into it.",
            escalated=True,
            reasons=[EscalationReason.COMPLAINT],
            review_id=review,
        )
    )
    with client_with(service) as client:
        body = client.post(PATH, json={"message": "this is unacceptable"}).json()
    assert body["escalated"] is True
    assert body["escalation_reasons"] == ["complaint"]
    assert body["review_id"] == str(review)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": ""},
        {"message": "x" * (MAX_MESSAGE_LENGTH + 1)},
        {"message": "hi", "unexpected": 1},
        {"message": 42},
    ],
)
def test_invalid_bodies_are_rejected(payload: dict) -> None:
    with client_with(service_of()) as client:
        assert client.post(PATH, json=payload).status_code == 422


def test_a_message_at_the_limit_is_accepted() -> None:
    with client_with(service_of()) as client:
        response = client.post(PATH, json={"message": "x" * MAX_MESSAGE_LENGTH})
    assert response.status_code == 200


def test_a_provider_outage_maps_to_its_own_status() -> None:
    """Not caught and reflattened into a 500 -- M1's handler already renders it."""
    service = service_of(intent=StubIntent(error=LLMUnavailableError()))
    with client_with(service) as client:
        response = client.post(PATH, json={"message": "hello"})
    assert response.status_code == LLMUnavailableError.status_code
    body = response.json()
    assert body["code"] == LLMUnavailableError.code
    assert set(body) <= {"code", "message", "details"}


def test_the_response_publishes_no_internal_objects() -> None:
    """The contract is the schema, not whatever the service happens to hold."""
    with client_with(service_of()) as client:
        body = client.post(PATH, json={"message": "hello"}).json()
    assert set(body) == set(AssistantMessageResponse.model_fields)


def test_the_service_value_and_the_response_are_separate_types() -> None:
    """So a domain field can be added without publishing it."""
    assert AssistantReply is not AssistantMessageResponse
    assert "route_reason" in AssistantReply.model_fields


def test_health_and_readiness_are_untouched() -> None:
    with client_with(service_of()) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/health").status_code == 404  # removed alias
        assert client.get("/api/v1/ready").status_code in (200, 503)
