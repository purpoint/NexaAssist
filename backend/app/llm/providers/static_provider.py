"""A deterministic provider that never touches the network.

Two jobs. It lets the application run without provider credentials -- local
development and CI -- and it keeps the abstraction honest, since an interface
with a single implementation has not been shown to be an interface at all.

Responses are canned per schema. Asking for a schema with no canned response
raises rather than inventing an answer: a fabricated completion that looks real
is worse than a loud failure.
"""

from typing import TypeVar, cast

from pydantic import BaseModel

from app.llm.base import LLMConfig, LLMPrompt, LLMUsage, StructuredCompletion
from app.llm.errors import LLMError

T = TypeVar("T", bound=BaseModel)

DEFAULT_CANNED_RESPONSES: dict[type[BaseModel], BaseModel] = {}
"""Schema -> canned instance, populated as capabilities are added."""


class StaticLLMProvider:
    """Returns a fixed response per schema. No network, no randomness."""

    name = "static"

    def __init__(
        self,
        config: LLMConfig,
        canned: dict[type[BaseModel], BaseModel] | None = None,
    ) -> None:
        self._config = config
        self._canned = dict(DEFAULT_CANNED_RESPONSES if canned is None else canned)

    async def complete_structured(
        self,
        *,
        prompt: LLMPrompt,
        schema: type[T],
        config: LLMConfig | None = None,
    ) -> StructuredCompletion[T]:
        """Return the canned instance registered for ``schema``."""
        try:
            canned = self._canned[schema]
        except KeyError:
            raise LLMError(
                f"The static provider has no canned response for {schema.__name__}.",
                details={"provider": self.name, "schema": schema.__name__},
            ) from None

        effective = config or self._config
        # A copy, so a caller mutating the result cannot corrupt later calls.
        return StructuredCompletion[schema](
            output=cast(T, canned.model_copy(deep=True)),
            provider=self.name,
            model=effective.model,
            stop_reason="end_turn",
            usage=LLMUsage(),
            latency_ms=0.0,
        )
