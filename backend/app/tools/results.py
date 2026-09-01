"""What a tool invocation produces.

Always a result, never an exception. A caller that has to distinguish "the
tool said no" from "the tool crashed" by catching exception types will get it
wrong eventually, and a model-driven caller cannot catch anything at all -- it
can only read what it is handed.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolOutcome(StrEnum):
    """How an invocation ended."""

    OK = "ok"
    INVALID_PARAMS = "invalid_params"
    FAILED = "failed"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"


class ToolResult(BaseModel):
    """The outcome of one tool call."""

    model_config = ConfigDict(frozen=True)

    tool: str
    outcome: ToolOutcome
    output: Any = Field(
        default=None, description="Present when the outcome is 'ok'."
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why it failed, phrased for the caller. Never an exception type, "
            "a traceback, or a database message."
        ),
    )
    retryable: bool = Field(
        default=False, description="Whether trying the same call again could help."
    )
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.outcome is ToolOutcome.OK

    @classmethod
    def success(cls, tool: str, output: Any, duration_ms: float = 0.0) -> "ToolResult":
        return cls(tool=tool, outcome=ToolOutcome.OK, output=output, duration_ms=duration_ms)

    @classmethod
    def failure(
        cls,
        tool: str,
        outcome: ToolOutcome,
        error: str,
        *,
        retryable: bool = False,
        duration_ms: float = 0.0,
    ) -> "ToolResult":
        return cls(
            tool=tool,
            outcome=outcome,
            error=error,
            retryable=retryable,
            duration_ms=duration_ms,
        )
