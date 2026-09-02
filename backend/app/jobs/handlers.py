"""What runs a job, and the set of things that can.

Mirrors the tool system on purpose. A handler declares its payload as a
Pydantic model rather than reading a free-form dict, so validation and the
payload's documented shape have one definition instead of two that drift --
the same reasoning as :class:`~app.tools.base.Tool`.

The registry is explicit rather than discovered by import. What a system will
do unattended, in the background, with no user watching, is exactly the list
that should be readable in one place.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.core.logging import get_logger
from app.jobs.base import MAX_NAME_LENGTH, validate_job_name
from app.jobs.errors import JobDefinitionError, JobNotFoundError

logger = get_logger(__name__)


class JobError(Exception):
    """A handler failed in a way its caller is expected to handle.

    Distinct from a bug: raising this says "this attempt did not work", and
    ``retryable`` says whether another one could. The same shape as
    :class:`~app.tools.base.ToolError`, for the same reason.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@runtime_checkable
class JobHandler(Protocol):
    """Work that a queued job's name selects."""

    name: str
    payload: type[BaseModel]

    async def run(self, params: BaseModel) -> None:
        """Do the work, with an already-validated payload.

        Returns nothing. A job's result is its effect -- rows written, mail
        sent -- and there is no caller waiting to read a value back.
        """
        ...


class JobHandlerRegistry:
    """Name-to-handler mapping, validated at registration time."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, handler: JobHandler) -> None:
        name = validate_job_name(getattr(handler, "name", ""))
        if not isinstance(getattr(handler, "payload", None), type) or not issubclass(
            handler.payload, BaseModel
        ):
            raise JobDefinitionError(
                "A job handler must declare its payload as a Pydantic model.",
                details={"name": name},
            )
        if name in self._handlers:
            # Silently replacing would make behaviour depend on import order.
            raise JobDefinitionError(
                f"A handler named {name!r} is already registered.",
                details={"name": name},
            )
        self._handlers[name] = handler
        logger.info("job handler registered name=%s", name)

    def get(self, name: str) -> JobHandler:
        try:
            return self._handlers[name]
        except KeyError:
            raise JobNotFoundError(
                "No handler is registered for that job.", details={"job_name": name}
            ) from None

    def has(self, name: str) -> bool:
        return name in self._handlers

    def names(self) -> list[str]:
        """Registered names, sorted, so listings are stable."""
        return sorted(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)


__all__ = [
    "MAX_NAME_LENGTH",
    "JobError",
    "JobHandler",
    "JobHandlerRegistry",
]
