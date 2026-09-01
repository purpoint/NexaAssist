"""Tool-system failures, expressed as application errors."""

from app.core.exceptions import AppError, NotFoundError


class ToolNotFoundError(NotFoundError):
    """No tool is registered under the requested name."""

    code = "tool_not_found"
    message = "The requested tool is not registered."


class ToolRegistrationError(AppError):
    """A tool could not be registered.

    A 500: the caller did nothing wrong. Raised at wiring time, so it surfaces
    when the application is built rather than on some later request.
    """

    status_code = 500
    code = "tool_registration_error"
    message = "The tool could not be registered."
