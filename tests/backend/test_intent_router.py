"""Routing, fallback, and ambiguity handling. Offline."""

import logging

import pytest

from app.routing.handlers import HandlerRequest, HandlerResponse
from app.routing.registry import HandlerRegistry
from app.routing.router import (
    DEFAULT_MIN_CONFIDENCE,
    IntentRouter,
    RouteReason,
)
from app.schemas.intent import IntentAnalysis, IntentCategory


class Recorder:
    def __init__(self, name: str, handled: bool = True) -> None:
        self.name = name
        self.handled = handled
        self.seen: list[HandlerRequest] = []

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        self.seen.append(request)
        return HandlerResponse(handler=self.name, reply=f"{self.name} reply", handled=self.handled)


def analysis(
    intent: IntentCategory = IntentCategory.BILLING, confidence: float = 0.9
) -> IntentAnalysis:
    return IntentAnalysis(intent=intent, confidence=confidence, reason="because")


def build(
    *, mapped: list[IntentCategory] | None = None, min_confidence: float = DEFAULT_MIN_CONFIDENCE
) -> tuple[IntentRouter, dict[str, Recorder]]:
    registry = HandlerRegistry()
    handlers: dict[str, Recorder] = {}
    for category in mapped if mapped is not None else list(IntentCategory):
        recorder = Recorder(category.value)
        handlers[category.value] = recorder
        registry.register(category, recorder)
    fallback = Recorder("fallback")
    handlers["fallback"] = fallback
    return IntentRouter(registry, fallback, min_confidence=min_confidence), handlers


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_confident_classification_reaches_its_handler() -> None:
    router, handlers = build()

    reply = await router.route("I was charged twice", analysis())

    assert reply.decision.reason is RouteReason.MATCHED
    assert reply.decision.fallback is False
    assert reply.decision.handler == "billing"
    assert handlers["billing"].seen[0].message == "I was charged twice"
    assert handlers["fallback"].seen == []


@pytest.mark.parametrize("category", list(IntentCategory))
def test_every_category_decides_without_error(category: IntentCategory) -> None:
    router, _ = build()

    decision = router.decide(analysis(category))

    assert decision.handler


def test_decide_has_no_side_effects() -> None:
    """The choice must be inspectable without running a handler."""
    router, handlers = build()

    router.decide(analysis())

    assert all(h.seen == [] for h in handlers.values())


# --------------------------------------------------------------------------
# Fallback: three distinct reasons, one destination
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_other_routes_to_fallback_as_no_category() -> None:
    router, handlers = build()

    reply = await router.route("tell me a joke", analysis(IntentCategory.OTHER, 0.99))

    assert reply.decision.reason is RouteReason.NO_CATEGORY
    assert reply.decision.fallback is True
    assert handlers["fallback"].seen


@pytest.mark.anyio
async def test_low_confidence_routes_to_fallback() -> None:
    """A confident-looking category the model was unsure of is a guess."""
    router, handlers = build(min_confidence=0.7)

    reply = await router.route("maybe billing?", analysis(IntentCategory.BILLING, 0.4))

    assert reply.decision.reason is RouteReason.LOW_CONFIDENCE
    assert reply.decision.fallback is True
    assert handlers["billing"].seen == []


@pytest.mark.anyio
async def test_an_unmapped_category_routes_to_fallback() -> None:
    router, handlers = build(mapped=[IntentCategory.BILLING])

    reply = await router.route("app crashes", analysis(IntentCategory.TECHNICAL_SUPPORT))

    assert reply.decision.reason is RouteReason.NO_HANDLER
    assert handlers["fallback"].seen


def test_the_three_fallback_reasons_remain_distinguishable() -> None:
    """They share a destination but not a cause; logs must tell them apart."""
    router, _ = build(mapped=[IntentCategory.BILLING], min_confidence=0.7)

    assert router.decide(analysis(IntentCategory.OTHER, 0.9)).reason is RouteReason.NO_CATEGORY
    assert router.decide(analysis(IntentCategory.BILLING, 0.1)).reason is RouteReason.LOW_CONFIDENCE
    assert (
        router.decide(analysis(IntentCategory.COMPLAINT, 0.9)).reason is RouteReason.NO_HANDLER
    )


# --------------------------------------------------------------------------
# Threshold behaviour
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(0.69, True), (0.70, False), (0.71, False)],
    ids=["below", "at", "above"],
)
def test_the_threshold_is_inclusive_at_the_boundary(
    confidence: float, expected: bool
) -> None:
    router, _ = build(min_confidence=0.7)

    assert router.decide(analysis(IntentCategory.BILLING, confidence)).fallback is expected


def test_a_zero_threshold_never_rejects_on_confidence() -> None:
    router, _ = build(min_confidence=0.0)

    assert router.decide(analysis(IntentCategory.BILLING, 0.0)).reason is RouteReason.MATCHED


# --------------------------------------------------------------------------
# Handler outcome
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_handler_that_could_not_resolve_is_reported_as_such() -> None:
    registry = HandlerRegistry()
    registry.register(IntentCategory.BILLING, Recorder("billing", handled=False))
    router = IntentRouter(registry, Recorder("fallback"))

    reply = await router.route("m", analysis())

    assert reply.handled is False
    assert reply.decision.reason is RouteReason.MATCHED


@pytest.mark.anyio
async def test_the_reply_comes_from_the_handler_that_ran() -> None:
    router, _ = build()

    reply = await router.route("m", analysis(IntentCategory.OTHER))

    assert reply.reply == "fallback reply"


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_logs_carry_the_decision_not_the_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    router, _ = build()

    with caplog.at_level(logging.INFO, logger="app.routing.router"):
        await router.route("my card 4242 was double charged", analysis())

    assert "intent=billing" in caplog.text and "reason=matched" in caplog.text
    assert "4242" not in caplog.text
    assert "billing reply" not in caplog.text
