"""Application error types and the handlers that render them.

Intentionally minimal: one base class, one concrete subclass to show the
pattern, and handlers that render errors through
:class:`~app.schemas.common.ErrorResponse` so clients only ever parse one
shape.

To add an error type, subclass :class:`AppError` and set the three class
attributes::

    class ConflictError(AppError):
        status_code = 409
        code = "conflict"
        message = "The resource is in a conflicting state."

Request-validation errors keep FastAPI's default 422 body for now -- no
endpoint accepts a request body yet, so a handler for them would be
unreachable code.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.schemas.common import ErrorResponse

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for errors the application raises deliberately."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, details: Any | None = None) -> None:
        self.message = message if message is not None else type(self).message
        self.details = details
        super().__init__(self.message)

    def to_response(self) -> ErrorResponse:
        """Render this error as the public error body."""
        return ErrorResponse(code=self.code, message=self.message, details=self.details)


class NotFoundError(AppError):
    """A requested resource does not exist."""

    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


def _code_for_status(status_code: int) -> str:
    """Derive an error code from a status code, e.g. 404 -> ``not_found``."""
    try:
        return HTTPStatus(status_code).phrase.lower().replace(" ", "_")
    except ValueError:
        return "http_error"


def _render(error: ErrorResponse, status_code: int, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error.model_dump(exclude_none=True),
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the application's exception handlers to ``app``."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("%s %s -> %s: %s", request.method, request.url.path, exc.code, exc.message)
        return _render(exc.to_response(), exc.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        error = ErrorResponse(
            code=_code_for_status(exc.status_code),
            message=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        )
        return _render(error, exc.status_code, headers=exc.headers)
