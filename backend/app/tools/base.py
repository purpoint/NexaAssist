"""What a tool is.

A tool is a named, described, schema-validated operation the system can invoke.
Parameters are declared as a Pydantic model rather than a free-form dict: the
model doubles as validation and as the JSON Schema a model-driven caller will
need later, so the contract has one definition rather than two that drift.
"""

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

MAX_NAME_LENGTH = 64


class ToolError(Exception):
    """A tool failed in a way its caller is expected to handle.

    Distinct from a bug: raising this says "this call cannot succeed", which
    the executor turns into a failed result rather than letting it escape.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@runtime_checkable
class Tool(Protocol):
    """A callable capability with a declared parameter schema."""

    name: str
    description: str
    parameters: type[BaseModel]

    async def run(self, params: BaseModel) -> Any:
        """Execute with already-validated parameters."""
        ...


def parameters_schema(tool: Tool) -> dict[str, Any]:
    """JSON Schema for a tool's parameters.

    Kept as a function rather than a method so a tool has nothing to implement
    beyond its own behaviour.
    """
    return tool.parameters.model_json_schema()


def describe(tool: Tool) -> dict[str, Any]:
    """A tool's public description: what a caller needs to choose and call it."""
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": parameters_schema(tool),
    }
