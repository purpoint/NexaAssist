"""The workflow definition format. Offline."""

import pytest
from pydantic import ValidationError

from app.workflows.definition import (
    MAX_STEPS,
    Workflow,
    WorkflowStep,
    reference_to,
    referenced_step,
)


def step(id: str = "lookup", tool: str = "lookup_ticket", **kw: object) -> WorkflowStep:
    return WorkflowStep(id=id, tool=tool, **kw)


def workflow(*steps: WorkflowStep, name: str = "refund_check") -> Workflow:
    return Workflow(name=name, description="A workflow.", steps=list(steps) or [step()])


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_a_minimal_workflow_validates() -> None:
    definition = workflow()

    assert definition.name == "refund_check"
    assert definition.step_ids() == ["lookup"]


def test_definitions_are_immutable() -> None:
    """A definition is inert data; it must not change under a running engine."""
    definition = workflow()

    with pytest.raises(Exception):
        definition.name = "other"


def test_a_workflow_needs_at_least_one_step() -> None:
    with pytest.raises(ValidationError):
        Workflow(name="w", description="d", steps=[])


def test_a_workflow_needs_a_description() -> None:
    with pytest.raises(ValidationError):
        Workflow(name="w", description="", steps=[step()])


def test_step_count_is_bounded() -> None:
    many = [step(id=f"s{i}") for i in range(MAX_STEPS + 1)]

    with pytest.raises(ValidationError):
        Workflow(name="w", description="d", steps=many)


def test_tools_used_is_deduplicated_in_first_use_order() -> None:
    definition = workflow(
        step("a", "search_knowledge_base"), step("b", "lookup_ticket"), step("c", "search_knowledge_base")
    )

    assert definition.tools_used() == ["search_knowledge_base", "lookup_ticket"]


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["Refund", "refund-check", "1refund", "refund check", ""])
def test_invalid_workflow_names_are_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        Workflow(name=bad, description="d", steps=[step()])


@pytest.mark.parametrize("bad", ["Lookup", "look-up", "2nd", ""])
def test_invalid_step_ids_are_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        step(id=bad)


def test_invalid_tool_names_are_rejected() -> None:
    with pytest.raises(ValidationError):
        step(tool="Lookup-Ticket")


def test_duplicate_step_ids_are_rejected() -> None:
    """A duplicate id would make a reference ambiguous."""
    with pytest.raises(ValidationError, match="Duplicate step id"):
        workflow(step("a"), step("a"))


# --------------------------------------------------------------------------
# References
# --------------------------------------------------------------------------


def test_a_step_can_reference_an_earlier_output() -> None:
    definition = workflow(
        step("lookup"),
        step("search", "search_knowledge_base", params={"query": reference_to("lookup")}),
    )

    assert definition.steps[1].references() == {"lookup"}


def test_literal_parameters_are_not_references() -> None:
    assert step(params={"query": "refund"}).references() == set()


def test_non_string_parameters_are_ignored() -> None:
    assert step(params={"top_k": 3, "flag": True}).references() == set()


def test_forward_references_are_rejected() -> None:
    """A workflow is a sequence; a step cannot use what has not run."""
    with pytest.raises(ValidationError, match="do not appear before it"):
        workflow(
            step("first", params={"q": reference_to("later")}),
            step("later"),
        )


def test_self_references_are_rejected() -> None:
    with pytest.raises(ValidationError, match="do not appear before it"):
        workflow(step("only", params={"q": reference_to("only")}))


def test_unknown_references_are_rejected() -> None:
    with pytest.raises(ValidationError, match="do not appear before it"):
        workflow(step("a", params={"q": reference_to("ghost")}))


@pytest.mark.parametrize(
    "value",
    ["{{ steps.lookup.output }}", "{{steps.lookup.output}}"],
    ids=["spaced", "tight"],
)
def test_reference_syntax_tolerates_spacing(value: str) -> None:
    assert referenced_step(value) == "lookup"


@pytest.mark.parametrize(
    "value",
    ["see {{ steps.lookup.output }} here", "{{ steps.lookup.status }}", "steps.lookup.output", 42],
    ids=["interpolated", "wrong-field", "no-braces", "not-a-string"],
)
def test_partial_or_malformed_references_are_literals(value: object) -> None:
    """Only whole-value references are supported.

    Interpolating a structured result into a larger string would force a
    stringification whose format nothing has agreed on.
    """
    assert referenced_step(value) is None


def test_reference_helper_round_trips() -> None:
    assert referenced_step(reference_to("lookup")) == "lookup"
