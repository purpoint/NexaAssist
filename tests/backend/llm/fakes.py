"""Test doubles for the LLM layer.

Lives in ``tests/`` rather than ``app/`` because it is scaffolding, not a
shipped capability. ``StaticLLMProvider`` is the shipped offline provider; this
fake exists to record call arguments and to raise on demand.
"""

from typing import TypeVar, cast

from pydantic import BaseModel

from app.llm.base import LLMConfig, LLMPrompt, LLMUsage, StructuredCompletion

T = TypeVar("T", bound=BaseModel)


class FakeLLMProvider:
    """Returns a configured output, or raises a configured error.

    Satisfies the ``LLMProvider`` protocol structurally -- it neither imports
    nor inherits from it, which is the point of using a Protocol.
    """

    name = "fake"

    def __init__(
        self,
        output: BaseModel | None = None,
        error: Exception | None = None,
    ) -> None:
        self._output = output
        self._error = error
        self.calls: list[tuple[LLMPrompt, type[BaseModel]]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def complete_structured(
        self,
        *,
        prompt: LLMPrompt,
        schema: type[T],
        config: LLMConfig | None = None,
    ) -> StructuredCompletion[T]:
        self.calls.append((prompt, schema))
        if self._error is not None:
            raise self._error
        if self._output is None:
            raise AssertionError("FakeLLMProvider needs an output or an error.")
        return StructuredCompletion[schema](
            output=cast(T, self._output),
            provider=self.name,
            model="fake-model",
            stop_reason="stop",
            usage=LLMUsage(input_tokens=11, output_tokens=22),
            latency_ms=1.5,
        )
