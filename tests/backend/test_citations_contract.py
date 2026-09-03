"""Citations reaching the frontend contract, and the cases where they must not.

M5 built citations and M8 dropped them on the floor -- ``HandlerResponse`` had
nowhere to put them. These tests pin the plumbing, and pin the one case where
carrying them would be a lie.
"""

import uuid

import pytest

from app.policy.enforcement import PolicyEnforcer
from app.policy.library import default_rules
from app.policy.rules import PolicyEngine
from app.routing.handlers import HandlerRequest, HandlerResponse
from app.routing.registry import HandlerRegistry
from app.routing.router import IntentRouter
from app.schemas.assistant import AssistantMessageResponse
from app.schemas.document import Citation
from app.schemas.intent import IntentAnalysis, IntentCategory

pytestmark = pytest.mark.anyio


def citation(title: str = "Refunds") -> Citation:
    return Citation(
        document_id=uuid.uuid4(),
        document_title=title,
        ordinal=0,
        excerpt="Refunds take five business days.",
        similarity=0.9,
    )


class Citing:
    """A handler that answers from documentation."""

    name = "knowledge_base"

    def __init__(self, reply: str = "Five business days.", handled: bool = True) -> None:
        self._reply = reply
        self._handled = handled

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        return HandlerResponse(
            handler=self.name,
            reply=self._reply,
            handled=self._handled,
            citations=[citation()],
        )


class Plain:
    """A handler that answers from nothing in particular."""

    name = "agent"

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        return HandlerResponse(handler=self.name, reply="Looks fine to me.")


def router_over(handler: object, *, with_policy: bool = True) -> IntentRouter:
    registry = HandlerRegistry()
    for category in IntentCategory:
        registry.register(category, handler)
    return IntentRouter(
        registry,
        handler,
        enforcer=PolicyEnforcer(PolicyEngine(default_rules())) if with_policy else None,
    )


def analysis(intent: IntentCategory = IntentCategory.PRODUCT_QUESTION) -> IntentAnalysis:
    return IntentAnalysis(intent=intent, confidence=0.95, reason="fixture")


# --------------------------------------------------------------------------
# The extension is additive


def test_a_handler_response_needs_no_citations() -> None:
    """Every handler that predates this keeps working unchanged."""
    response = HandlerResponse(handler="agent", reply="hello")
    assert response.citations == []


def test_a_plain_handler_produces_no_citations() -> None:
    assert HandlerResponse(handler="agent", reply="hi").citations == []


# --------------------------------------------------------------------------
# They reach the router


async def test_citations_survive_routing() -> None:
    routed = await router_over(Citing()).route("How long do refunds take?", analysis())
    assert [c.document_title for c in routed.citations] == ["Refunds"]
    assert routed.policy_modified is False


async def test_a_handler_without_citations_yields_none() -> None:
    routed = await router_over(Plain()).route("anything", analysis())
    assert routed.citations == []


# --------------------------------------------------------------------------
# They are dropped when policy rewrites the reply


async def test_policy_replacing_the_reply_drops_the_citations() -> None:
    """Provenance for text that is no longer being sent is a false claim."""
    router = router_over(Citing(reply="I have issued a full refund to your card."))
    routed = await router.route("I want my money back", analysis(IntentCategory.BILLING))

    assert routed.policy_modified is True
    assert routed.reply != "I have issued a full refund to your card."
    assert routed.citations == []


async def test_citations_are_kept_when_policy_leaves_the_reply_alone() -> None:
    """The guard must not be 'drop them whenever policy ran'."""
    router = router_over(Citing())
    routed = await router.route("How long do refunds take?", analysis())
    assert routed.policy_modified is False
    assert routed.citations != []


# --------------------------------------------------------------------------
# The published shape


def test_the_response_publishes_citations() -> None:
    assert "citations" in AssistantMessageResponse.model_fields


def test_a_citation_carries_enough_to_check_the_claim() -> None:
    assert set(Citation.model_fields) == {
        "document_id",
        "document_title",
        "ordinal",
        "excerpt",
        "similarity",
    }


def test_the_response_defaults_to_no_citations() -> None:
    """A client can rely on the field existing rather than testing for it."""
    response = AssistantMessageResponse(
        reply="hi",
        intent=IntentCategory.OTHER,
        confidence=0.0,
        handler="fallback",
        route_reason="no_category",
        fallback=True,
        handled=False,
    )
    assert response.citations == []
