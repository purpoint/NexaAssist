"""The handler contract and the intent-to-handler mapping. Offline."""

import pytest
from pydantic import ValidationError

from app.routing.errors import HandlerRegistrationError
from app.routing.handlers import HandlerRequest, HandlerResponse, IntentHandler
from app.routing.registry import HandlerRegistry
from app.schemas.intent import IntentAnalysis, IntentCategory

ANALYSIS = IntentAnalysis(intent=IntentCategory.BILLING, confidence=0.9, reason="r")


class Handler:
    name = "billing"

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        return HandlerResponse(handler=self.name, reply="ok")


def make(name: str = "billing") -> Handler:
    handler = Handler()
    handler.name = name
    return handler


def full_registry() -> HandlerRegistry:
    registry = HandlerRegistry()
    for category in IntentCategory:
        registry.register(category, make(category.value))
    return registry


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_a_handler_satisfies_the_protocol_structurally() -> None:
    assert isinstance(Handler(), IntentHandler)


def test_request_and_response_are_immutable() -> None:
    request = HandlerRequest(message="m", analysis=ANALYSIS)

    with pytest.raises(Exception):
        request.message = "other"


def test_a_reply_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        HandlerResponse(handler="h", reply="")


def test_a_handler_may_report_it_could_not_resolve() -> None:
    assert HandlerResponse(handler="h", reply="r", handled=False).handled is False


def test_request_requires_a_message() -> None:
    with pytest.raises(ValidationError):
        HandlerRequest(message="", analysis=ANALYSIS)


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_register_and_get() -> None:
    registry = HandlerRegistry()
    handler = make()
    registry.register(IntentCategory.BILLING, handler)

    assert registry.get(IntentCategory.BILLING) is handler
    assert registry.has(IntentCategory.BILLING)
    assert len(registry) == 1


def test_an_unmapped_intent_returns_none() -> None:
    assert HandlerRegistry().get(IntentCategory.BILLING) is None


def test_duplicate_registration_is_rejected() -> None:
    """Replacing silently would make routing depend on import order."""
    registry = HandlerRegistry()
    registry.register(IntentCategory.BILLING, make())

    with pytest.raises(HandlerRegistrationError, match="already has a handler"):
        registry.register(IntentCategory.BILLING, make("other"))


def test_unnamed_handlers_are_rejected() -> None:
    with pytest.raises(HandlerRegistrationError, match="named"):
        HandlerRegistry().register(IntentCategory.BILLING, make(""))


def test_registration_errors_are_application_errors() -> None:
    from app.core.exceptions import AppError

    assert issubclass(HandlerRegistrationError, AppError)
    assert HandlerRegistrationError().status_code == 500


# --------------------------------------------------------------------------
# Completeness
# --------------------------------------------------------------------------


def test_a_partial_registry_reports_what_is_missing() -> None:
    registry = HandlerRegistry()
    registry.register(IntentCategory.BILLING, make())

    missing = registry.unmapped()

    assert IntentCategory.BILLING not in missing
    assert IntentCategory.OTHER in missing


def test_require_complete_fails_while_categories_are_unmapped() -> None:
    """An unmapped category looks like classifier drift, not a missing route."""
    registry = HandlerRegistry()
    registry.register(IntentCategory.BILLING, make())

    with pytest.raises(HandlerRegistrationError) as excinfo:
        registry.require_complete()

    assert "technical_support" in excinfo.value.details["unmapped"]


def test_require_complete_passes_when_every_category_is_mapped() -> None:
    full_registry().require_complete()


def test_every_category_is_covered_by_a_complete_registry() -> None:
    registry = full_registry()

    assert len(registry) == len(list(IntentCategory))
    assert registry.unmapped() == []


def test_mapping_is_inspectable_and_stable() -> None:
    mapping = full_registry().mapping()

    assert mapping["billing"] == "billing"
    assert list(mapping) == sorted(mapping)
