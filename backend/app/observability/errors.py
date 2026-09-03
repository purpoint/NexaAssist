"""Observability failures, expressed as application errors."""

from app.core.exceptions import AppError


class TracingConfigurationError(AppError):
    """A tracer was asked for a recorder that does not exist."""

    status_code = 500
    code = "tracing_configuration_error"
    message = "The tracing configuration is not valid."
