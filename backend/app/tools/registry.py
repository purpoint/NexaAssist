"""The set of tools available to the application.

An explicit registry rather than import-time auto-discovery: what a system can
be asked to *do* should be visible in one readable place, and a tool that
appears merely because a module got imported is how surprising capabilities
arrive.
"""

from collections.abc import Iterator

from app.core.logging import get_logger
from app.tools.base import MAX_NAME_LENGTH, Tool, describe
from app.tools.errors import ToolNotFoundError, ToolRegistrationError

logger = get_logger(__name__)

_ALLOWED_NAME = set("abcdefghijklmnopqrstuvwxyz0123456789_")


class ToolRegistry:
    """Name-to-tool mapping with validation at registration time."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Add a tool, rejecting anything malformed or duplicated."""
        name = getattr(tool, "name", "")
        if not name or not set(name) <= _ALLOWED_NAME:
            raise ToolRegistrationError(
                "Tool names must be non-empty and use only lowercase letters, "
                "digits, and underscores.",
                details={"name": str(name)},
            )
        if len(name) > MAX_NAME_LENGTH:
            raise ToolRegistrationError(
                f"Tool names must be at most {MAX_NAME_LENGTH} characters.",
                details={"name": name},
            )
        if not getattr(tool, "description", ""):
            raise ToolRegistrationError(
                "Tools must carry a description; a caller cannot choose "
                "between undescribed capabilities.",
                details={"name": name},
            )
        if name in self._tools:
            # Silently replacing would make behaviour depend on import order.
            raise ToolRegistrationError(
                f"A tool named {name!r} is already registered.",
                details={"name": name},
            )

        self._tools[name] = tool
        logger.info("tool registered name=%s", name)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(details={"tool": name}) from None

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        """Registered names, sorted, so listings are stable."""
        return sorted(self._tools)

    def describe_all(self) -> list[dict[str, object]]:
        """Every tool's public description, in a stable order."""
        return [describe(self._tools[name]) for name in self.names()]

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return (self._tools[name] for name in self.names())
