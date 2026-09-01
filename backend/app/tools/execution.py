"""Running tools safely.

The executor is the boundary where anything can go wrong and nothing may
escape. It validates parameters, bounds the call in time, and converts every
failure into a :class:`~app.tools.results.ToolResult`.

Two rules it exists to enforce:

* **No exception escapes.** A tool is often invoked on behalf of a model, which
  cannot catch anything; an escaping exception would abort a whole turn.
* **No internals leak.** A driver message, a traceback, or a connection string
  in an error field would travel straight back into a prompt, and from there
  potentially to a user. Unexpected failures are reported generically and
  logged by type.
"""

import asyncio
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.tools.base import Tool, ToolError
from app.tools.errors import ToolNotFoundError
from app.tools.registry import ToolRegistry
from app.tools.results import ToolOutcome, ToolResult

logger = get_logger(__name__)

DEFAULT_TOOL_TIMEOUT_SECONDS = 15.0

UNEXPECTED_ERROR = "The tool failed unexpectedly."


class ToolExecutor:
    """Validates, runs, and times a tool call."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    ) -> None:
        self._registry = registry
        self._timeout = timeout_seconds

    async def execute(self, name: str, params: dict[str, Any] | None = None) -> ToolResult:
        """Run ``name`` with ``params``, returning a result whatever happens."""
        started = time.perf_counter()

        try:
            tool = self._registry.get(name)
        except ToolNotFoundError:
            return self._fail(
                name,
                ToolOutcome.NOT_FOUND,
                f"No tool named {name!r} is available.",
                started,
            )

        try:
            validated = tool.parameters.model_validate(params or {})
        except ValidationError as exc:
            # Field paths and messages only. Pydantic includes the offending
            # input in its rendering, which may be user content.
            return self._fail(
                name,
                ToolOutcome.INVALID_PARAMS,
                _describe_validation(exc),
                started,
            )

        return await self._run(tool, validated, started)

    async def _run(self, tool: Tool, params: BaseModel, started: float) -> ToolResult:
        try:
            async with asyncio.timeout(self._timeout):
                output = await tool.run(params)
        except TimeoutError:
            return self._fail(
                tool.name,
                ToolOutcome.TIMEOUT,
                f"The tool did not finish within {self._timeout:g}s.",
                started,
                retryable=True,
            )
        except ToolError as exc:
            # A deliberate, expected failure: the message was written for a
            # caller to read.
            return self._fail(
                tool.name, ToolOutcome.FAILED, exc.message, started, retryable=exc.retryable
            )
        except Exception as exc:
            # A bug or an unavailable dependency. The type is logged; the
            # caller is told nothing that could carry internals into a prompt.
            logger.warning(
                "tool raised an unexpected error name=%s error=%s",
                tool.name,
                type(exc).__name__,
            )
            return self._fail(tool.name, ToolOutcome.FAILED, UNEXPECTED_ERROR, started)

        duration = _elapsed_ms(started)
        logger.info(
            "tool executed name=%s outcome=%s duration_ms=%.1f",
            tool.name,
            ToolOutcome.OK.value,
            duration,
        )
        return ToolResult.success(tool.name, output, duration_ms=duration)

    def _fail(
        self,
        name: str,
        outcome: ToolOutcome,
        error: str,
        started: float,
        *,
        retryable: bool = False,
    ) -> ToolResult:
        duration = _elapsed_ms(started)
        logger.info(
            "tool failed name=%s outcome=%s duration_ms=%.1f", name, outcome.value, duration
        )
        return ToolResult.failure(
            name, outcome, error, retryable=retryable, duration_ms=duration
        )


def _describe_validation(exc: ValidationError) -> str:
    parts = [
        f"{'.'.join(str(p) for p in err['loc']) or 'body'}: {err['msg']}"
        for err in exc.errors()
    ]
    return "Invalid parameters — " + "; ".join(parts)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
