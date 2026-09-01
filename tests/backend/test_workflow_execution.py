"""Workflow execution. Offline, over stub tools."""

import logging
import uuid
from typing import Any

import pytest
from pydantic import BaseModel

from app.tools.base import ToolError
from app.tools.execution import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.results import ToolOutcome
from app.workflows.definition import Workflow, WorkflowStep, reference_to
from app.workflows.execution import WorkflowRunner, unresolved_references


class AnyParams(BaseModel):
    value: Any = None


def tool(name: str, behaviour: Any) -> Any:
    class Made:
        pass

    made = Made()
    made.name = name
    made.description = f"The {name} tool."
    made.parameters = AnyParams
    made.run = behaviour
    return made


async def echoes(params: AnyParams) -> Any:
    return {"echoed": params.value}


async def fails(params: AnyParams) -> Any:
    raise ToolError("upstream refused")


async def requires_value(params: AnyParams) -> Any:
    if params.value is None:
        raise ToolError("value is required")
    return {"got": params.value}


def runner(*tools: Any) -> WorkflowRunner:
    registry = ToolRegistry()
    for t in tools or [tool("echo", echoes)]:
        registry.register(t)
    return WorkflowRunner(ToolExecutor(registry))


def wf(*steps: WorkflowStep, name: str = "demo", inputs: list[str] | None = None) -> Workflow:
    return Workflow(name=name, description="d", inputs=inputs or [], steps=list(steps))


def step(id: str, tool_name: str = "echo", **kw: Any) -> WorkflowStep:
    return WorkflowStep(id=id, tool=tool_name, **kw)


# --------------------------------------------------------------------------
# Sequencing
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_single_step_workflow_completes() -> None:
    run, outputs = await runner().run(wf(step("a", params={"value": 1})))

    assert run.completed is True
    assert [r.id for r in run.steps] == ["a"]
    assert run.steps[0].outcome is ToolOutcome.OK
    assert outputs["a"] == {"echoed": 1}
    uuid.UUID(run.run_id)


@pytest.mark.anyio
async def test_steps_run_in_declared_order() -> None:
    run, _ = await runner().run(
        wf(step("first", params={"value": 1}), step("second", params={"value": 2}))
    )

    assert [r.id for r in run.steps] == ["first", "second"]


@pytest.mark.anyio
async def test_a_later_step_receives_an_earlier_output() -> None:
    """The whole point of references: steps compose."""
    run, outputs = await runner(tool("echo", echoes), tool("consume", requires_value)).run(
        wf(
            step("a", params={"value": 7}),
            step("b", "consume", params={"value": reference_to("a")}),
        )
    )

    assert run.completed is True
    assert outputs["b"] == {"got": {"echoed": 7}}


@pytest.mark.anyio
async def test_inputs_are_available_as_references() -> None:
    run, outputs = await runner().run(
        wf(step("a", params={"value": reference_to("ticket_id")}), inputs=["ticket_id"]),
        inputs={"ticket_id": "t-1"},
    )

    assert run.completed is True
    assert outputs["a"] == {"echoed": "t-1"}


@pytest.mark.anyio
async def test_literal_parameters_pass_through_unchanged() -> None:
    _, outputs = await runner().run(wf(step("a", params={"value": "refund"})))

    assert outputs["a"] == {"echoed": "refund"}


# --------------------------------------------------------------------------
# Failure
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_failed_step_stops_the_run() -> None:
    run, _ = await runner(tool("echo", echoes), tool("boom", fails)).run(
        wf(step("a"), step("b", "boom"), step("c"))
    )

    assert run.completed is False
    assert run.failed_step == "b"
    assert [r.id for r in run.steps] == ["a", "b"]  # 'c' never ran


@pytest.mark.anyio
async def test_an_optional_step_may_fail_without_stopping_the_run() -> None:
    run, outputs = await runner(tool("echo", echoes), tool("boom", fails)).run(
        wf(step("a"), step("b", "boom", optional=True), step("c", params={"value": 3}))
    )

    assert run.completed is True
    assert run.failed_step is None
    assert [r.outcome for r in run.steps] == [
        ToolOutcome.OK,
        ToolOutcome.FAILED,
        ToolOutcome.OK,
    ]
    assert "b" not in outputs


@pytest.mark.anyio
async def test_referencing_a_skipped_optional_step_fails_that_step_not_the_engine() -> None:
    """Resolving to None hands the executor invalid params -- a recorded
    outcome rather than a crash."""
    run, _ = await runner(tool("boom", fails), tool("consume", requires_value)).run(
        wf(
            step("a", "boom", optional=True),
            step("b", "consume", params={"value": reference_to("a")}),
        )
    )

    assert run.completed is False
    assert run.failed_step == "b"
    assert run.steps[1].outcome is ToolOutcome.FAILED


@pytest.mark.anyio
async def test_an_unknown_tool_is_a_recorded_failure() -> None:
    run, _ = await runner().run(wf(step("a", "missing_tool")))

    assert run.completed is False
    assert run.steps[0].outcome is ToolOutcome.NOT_FOUND


@pytest.mark.anyio
async def test_the_engine_never_raises() -> None:
    for definition in (
        wf(step("a", "missing_tool")),
        wf(step("a", "boom")),
    ):
        run, _ = await runner(tool("boom", fails)).run(definition)
        assert run.completed is False


# --------------------------------------------------------------------------
# Static checking
# --------------------------------------------------------------------------


def test_unresolved_references_are_reported_before_running() -> None:
    definition = wf(
        step("a", params={"value": reference_to("ticket_id")}), inputs=["ticket_id"]
    )

    assert unresolved_references(definition, set()) == {"ticket_id"}
    assert unresolved_references(definition, {"ticket_id"}) == set()


def test_internally_satisfied_references_are_not_reported() -> None:
    definition = wf(step("a"), step("b", params={"value": reference_to("a")}))

    assert unresolved_references(definition, set()) == set()


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_summary_excludes_step_outputs() -> None:
    """A run summary may be logged; outputs carry customer content."""
    run, outputs = await runner().run(
        wf(step("a", params={"value": "card 4242 charged twice"}))
    )

    assert "4242" not in str(run.model_dump())
    assert "4242" in str(outputs)  # the caller still gets the data


@pytest.mark.anyio
async def test_the_summary_is_serialisable() -> None:
    import json

    run, _ = await runner().run(wf(step("a")))

    json.dumps(run.model_dump())


@pytest.mark.anyio
async def test_logs_record_the_run_shape_not_its_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.workflows.execution"):
        await runner().run(wf(step("a", params={"value": "card 4242"})))

    assert "workflow=demo" in caplog.text and "steps=1" in caplog.text
    assert "4242" not in caplog.text
