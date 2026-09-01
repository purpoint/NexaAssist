"""Routing failures, expressed as application errors."""

from app.core.exceptions import AppError


class HandlerRegistrationError(AppError):
    """A handler could not be registered.

    Raised at wiring time so it surfaces when the application is assembled
    rather than on some later request.
    """

    status_code = 500
    code = "handler_registration_error"
    message = "The intent handler could not be registered."
