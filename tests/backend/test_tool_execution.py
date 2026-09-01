"""Tool execution and result handling. Offline."""

import asyncio
import logging
from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.tools.base import ToolError
from app.tools.execution import UNEXPECTED_ERROR, ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.results import ToolOutcome, ToolResult

LEAK = "connection to postgresql://user:pw@host/db refused"


class Params(BaseModel):
    value: int = Field(ge=0)


def tool(name: str, behaviour: Any) -> Any:
    class Made:
        pass

    made = Made()
    made.name = name
    made.description = "Test tool."
    made.parameters = Params

    async def run(params: Params) -> Any:
        return await behaviour(params)

    made.run = run
    return made


async def echo(params: Params) -> int:
    return params.value * 2


async def deliberate(params: Params) -> Any:
    raise ToolError("The account is closed.", retryable=False)


async def deliberate_retryable(params: Params) -> Any:
    raise ToolError("Upstream is busy.", retryable=True)


async def crash(params: Params) -> Any:
    raise RuntimeError(LEAK)


async def hang(params: Params) -> Any:
    await asyncio.sleep(5)


def executor(*tools: Any, timeout: float = 15.0) -> ToolExecutor:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    return ToolExecutor(registry, timeout_seconds=timeout)


# --------------------------------------------------------------------------
# Success
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_successful_call() -> None:
    result = await executor(tool("echo", echo)).execute("echo", {"value": 21})

    assert result.ok
    assert result.outcome is ToolOutcome.OK
    assert result.output == 42
    assert result.error is None
    assert result.duration_ms >= 0.0


@pytest.mark.anyio
async def test_missing_params_default_to_empty() -> None:
    """A tool whose parameters are all optional is callable with nothing."""

    class Optional(BaseModel):
        value: int = 7

    made = tool("opt", echo)
    made.parameters = Optional

    assert (await executor(made).execute("opt")).output == 14


# --------------------------------------------------------------------------
# Failure modes, each a result rather than an exception
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_tool_is_a_result_not_an_exception() -> None:
    result = await executor().execute("nope")

    assert result.outcome is ToolOutcome.NOT_FOUND
    assert not result.ok
    assert "nope" in result.error


@pytest.mark.anyio
async def test_invalid_parameters_are_reported_with_field_paths() -> None:
    result = await executor(tool("echo", echo)).execute("echo", {"value": -1})

    assert result.outcome is ToolOutcome.INVALID_PARAMS
    assert "value" in result.error


@pytest.mark.anyio
async def test_wrong_parameter_type_is_invalid_not_a_crash() -> None:
    result = await executor(tool("echo", echo)).execute("echo", {"value": "abc"})

    assert result.outcome is ToolOutcome.INVALID_PARAMS


@pytest.mark.anyio
async def test_deliberate_tool_error_keeps_its_message() -> None:
    result = await executor(tool("t", deliberate)).execute("t", {"value": 1})

    assert result.outcome is ToolOutcome.FAILED
    assert result.error == "The account is closed."
    assert result.retryable is False


@pytest.mark.anyio
async def test_retryable_flag_is_carried_through() -> None:
    result = await executor(tool("t", deliberate_retryable)).execute("t", {"value": 1})

    assert result.retryable is True


@pytest.mark.anyio
async def test_unexpected_exception_becomes_a_generic_failure() -> None:
    result = await executor(tool("t", crash)).execute("t", {"value": 1})

    assert result.outcome is ToolOutcome.FAILED
    assert result.error == UNEXPECTED_ERROR


@pytest.mark.anyio
async def test_unexpected_exception_never_leaks_internals() -> None:
    """The error field can travel into a prompt and onward to a user."""
    result = await executor(tool("t", crash)).execute("t", {"value": 1})

    for fragment in ("postgresql", "user:pw", "RuntimeError", "Traceback", "host/db"):
        assert fragment not in str(result.model_dump())


@pytest.mark.anyio
async def test_a_hanging_tool_times_out_and_is_retryable() -> None:
    result = await executor(tool("t", hang), timeout=0.05).execute("t", {"value": 1})

    assert result.outcome is ToolOutcome.TIMEOUT
    assert result.retryable is True
    assert "0.05" in result.error


@pytest.mark.anyio
@pytest.mark.parametrize(
    "behaviour", [deliberate, crash, hang], ids=["tool-error", "crash", "timeout"]
)
async def test_no_failure_mode_raises(behaviour: Any) -> None:
    """A model-driven caller cannot catch anything."""
    result = await executor(tool("t", behaviour), timeout=0.05).execute("t", {"value": 1})

    assert isinstance(result, ToolResult)
    assert not result.ok


# --------------------------------------------------------------------------
# Result shape
# --------------------------------------------------------------------------


def test_results_are_immutable() -> None:
    result = ToolResult.success("echo", 1)

    with pytest.raises(Exception):
        result.outcome = ToolOutcome.FAILED


def test_outcome_values() -> None:
    assert [o.value for o in ToolOutcome] == [
        "ok",
        "invalid_params",
        "failed",
        "timeout",
        "not_found",
    ]


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_logs_record_outcome_not_payloads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.tools.execution"):
        await executor(tool("echo", echo)).execute("echo", {"value": 4242})

    assert "name=echo" in caplog.text and "outcome=ok" in caplog.text
    assert "4242" not in caplog.text


@pytest.mark.anyio
async def test_crash_logs_only_the_exception_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.tools.execution"):
        await executor(tool("t", crash)).execute("t", {"value": 1})

    assert "RuntimeError" in caplog.text
    assert "postgresql" not in caplog.text
