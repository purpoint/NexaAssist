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

Request-validation errors are rendered through ``ErrorResponse`` as well. They
once kept FastAPI's default body, on the grounds that no endpoint accepted one;
that stopped being true at M4, and the default body embeds the offending
``input`` -- so a malformed request carrying a customer's message returned that
message, card numbers and all, straight back in the error. It is now the same
shape as every other error, and carries field paths only.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
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
    # Optional response headers. Subclasses override this (as a property or an
    # attribute) when the status calls for one -- e.g. Retry-After on a 429.
    headers: dict[str, str] | None = None

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
        return _render(exc.to_response(), exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Report *where* a body was wrong, never *what* it contained.

        Pydantic's rendering includes the offending value, which for these
        endpoints is a customer message. Field paths and messages are enough
        for a client to fix its request and carry nothing of the customer's.
        """
        fields = sorted(
            ".".join(str(part) for part in error["loc"][1:]) or "body"
            for error in exc.errors()
        )
        logger.info(
            "%s %s -> invalid_request fields=%s",
            request.method,
            request.url.path,
            ",".join(fields),
        )
        return _render(
            ErrorResponse(
                code="invalid_request",
                message="The request body is not valid.",
                details={"fields": fields},
            ),
            422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        error = ErrorResponse(
            code=_code_for_status(exc.status_code),
            message=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        )
        return _render(error, exc.status_code, headers=exc.headers)
