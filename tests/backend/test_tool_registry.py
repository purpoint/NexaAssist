"""The tool contract and the registry. Offline."""

from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.tools.base import MAX_NAME_LENGTH, Tool, ToolError, describe, parameters_schema
from app.tools.errors import ToolNotFoundError, ToolRegistrationError
from app.tools.registry import ToolRegistry


class EchoParams(BaseModel):
    message: str = Field(description="Text to echo back.")


class EchoTool:
    name = "echo"
    description = "Return the message it was given."
    parameters = EchoParams

    async def run(self, params: EchoParams) -> Any:
        return params.message


def make(name: str = "echo", description: str = "Does a thing.") -> Any:
    tool = EchoTool()
    tool.name = name
    tool.description = description
    return tool


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_a_tool_satisfies_the_protocol_structurally() -> None:
    assert isinstance(EchoTool(), Tool)


def test_parameters_schema_comes_from_the_pydantic_model() -> None:
    """One definition serves validation and the schema a caller reads."""
    schema = parameters_schema(EchoTool())

    assert schema["properties"]["message"]["description"] == "Text to echo back."
    assert schema["required"] == ["message"]


def test_describe_carries_what_a_caller_needs_to_choose_and_call() -> None:
    assert set(describe(EchoTool())) == {"name", "description", "parameters"}


def test_tool_error_defaults_to_not_retryable() -> None:
    assert ToolError("boom").retryable is False
    assert ToolError("busy", retryable=True).retryable is True


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_register_and_get() -> None:
    registry = ToolRegistry()
    tool = EchoTool()
    registry.register(tool)

    assert registry.get("echo") is tool
    assert registry.has("echo")
    assert len(registry) == 1


def test_unknown_tool_raises_a_404_error() -> None:
    with pytest.raises(ToolNotFoundError) as excinfo:
        ToolRegistry().get("nope")

    assert excinfo.value.status_code == 404
    assert excinfo.value.details == {"tool": "nope"}


def test_duplicate_names_are_rejected() -> None:
    """Silently replacing would make behaviour depend on import order."""
    registry = ToolRegistry()
    registry.register(make())

    with pytest.raises(ToolRegistrationError, match="already registered"):
        registry.register(make())


@pytest.mark.parametrize("name", ["", "Echo", "with space", "dash-name", "emoji✨"])
def test_malformed_names_are_rejected(name: str) -> None:
    with pytest.raises(ToolRegistrationError):
        ToolRegistry().register(make(name=name))


def test_overlong_names_are_rejected() -> None:
    with pytest.raises(ToolRegistrationError):
        ToolRegistry().register(make(name="a" * (MAX_NAME_LENGTH + 1)))


def test_a_name_at_the_limit_is_accepted() -> None:
    ToolRegistry().register(make(name="a" * MAX_NAME_LENGTH))


def test_tools_must_be_described() -> None:
    """A caller cannot choose between undescribed capabilities."""
    with pytest.raises(ToolRegistrationError, match="description"):
        ToolRegistry().register(make(description=""))


def test_registration_errors_are_application_errors() -> None:
    from app.core.exceptions import AppError

    assert issubclass(ToolRegistrationError, AppError)
    assert ToolRegistrationError().status_code == 500


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


def test_listings_are_stable_regardless_of_registration_order() -> None:
    registry = ToolRegistry()
    for name in ("zulu", "alpha", "mike"):
        registry.register(make(name=name))

    assert registry.names() == ["alpha", "mike", "zulu"]
    assert [d["name"] for d in registry.describe_all()] == ["alpha", "mike", "zulu"]
    assert [t.name for t in registry] == ["alpha", "mike", "zulu"]


def test_an_empty_registry_reports_nothing() -> None:
    registry = ToolRegistry()

    assert registry.names() == []
    assert registry.describe_all() == []
    assert not registry.has("echo")
    assert len(registry) == 0


def test_registries_are_independent() -> None:
    first, second = ToolRegistry(), ToolRegistry()
    first.register(EchoTool())

    assert not second.has("echo")
