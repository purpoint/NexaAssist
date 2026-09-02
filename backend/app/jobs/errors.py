"""Job-system failures, expressed as application errors."""

from app.core.exceptions import AppError, NotFoundError


class JobNotFoundError(NotFoundError):
    """No job is stored under the requested identifier."""

    code = "job_not_found"
    message = "The requested job was not found."


class JobPayloadError(AppError):
    """A payload could not be stored.

    A 500: this is a programming error at the enqueue site, not something a
    caller supplied. Raised eagerly so an unserialisable payload fails where it
    was written rather than inside a worker minutes later.
    """

    status_code = 500
    code = "job_payload_error"
    message = "The job payload could not be serialised."


class JobDefinitionError(AppError):
    """A job was enqueued with a malformed name or attempt budget.

    A 500 for the same reason as :class:`JobPayloadError`: nothing a caller
    sent produced this, so it is a wiring mistake and belongs to us.
    """

    status_code = 500
    code = "job_definition_error"
    message = "The job definition is not valid."


class JobQueueUnavailableError(AppError):
    """The queue backend could not be reached.

    A 503 rather than a 500: the request was fine and retrying may well work.
    Message is generic -- a client library's error can carry the connection
    string, and that string can carry a password.
    """

    status_code = 503
    code = "job_queue_unavailable"
    message = "The job queue is currently unavailable."
