"""Which handler serves which intent.

An explicit mapping rather than discovery: what happens to a customer's message
should be readable in one place, and a route that exists only because a module
was imported is how surprising behaviour arrives.

Every intent category must have a handler, checked at wiring time. A category
that silently falls through to the fallback because nobody noticed it was
unmapped is a routing bug that only shows up in production traffic.
"""

from app.core.logging import get_logger
from app.routing.errors import HandlerRegistrationError
from app.routing.handlers import IntentHandler
from app.schemas.intent import IntentCategory

logger = get_logger(__name__)


class HandlerRegistry:
    """Maps :class:`IntentCategory` to the handler that serves it."""

    def __init__(self) -> None:
        self._handlers: dict[IntentCategory, IntentHandler] = {}

    def register(self, intent: IntentCategory, handler: IntentHandler) -> None:
        if not getattr(handler, "name", ""):
            raise HandlerRegistrationError(
                "Handlers must be named so a reply can be attributed.",
                details={"intent": intent.value},
            )
        if intent in self._handlers:
            # Replacing silently would make routing depend on import order.
            raise HandlerRegistrationError(
                f"Intent {intent.value!r} already has a handler.",
                details={"intent": intent.value},
            )
        self._handlers[intent] = handler
        logger.info("handler registered intent=%s handler=%s", intent.value, handler.name)

    def get(self, intent: IntentCategory) -> IntentHandler | None:
        """The handler for ``intent``, or ``None`` if unmapped."""
        return self._handlers.get(intent)

    def has(self, intent: IntentCategory) -> bool:
        return intent in self._handlers

    def unmapped(self) -> list[IntentCategory]:
        """Categories with no handler, in declaration order."""
        return [c for c in IntentCategory if c not in self._handlers]

    def require_complete(self) -> None:
        """Fail unless every category is mapped.

        Called at wiring time: an unmapped category quietly becomes fallback
        traffic, which looks like the classifier underperforming rather than a
        missing route.
        """
        missing = self.unmapped()
        if missing:
            raise HandlerRegistrationError(
                "Every intent category needs a handler.",
                details={"unmapped": [c.value for c in missing]},
            )

    def __len__(self) -> int:
        return len(self._handlers)

    def mapping(self) -> dict[str, str]:
        """Intent name to handler name, for inspection and logging."""
        return {i.value: h.name for i, h in sorted(self._handlers.items(), key=lambda kv: kv[0].value)}
