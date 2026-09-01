"""Database failures, expressed as application errors.

Subclasses of :class:`~app.core.exceptions.AppError`, so the handlers
registered in M1 render them through ``ErrorResponse`` with no extra wiring.

No connection string, host, user, or driver message is ever placed in an error
message or in ``details`` -- a database error is one of the easiest ways to leak
credentials into a response body.
"""

from app.core.exceptions import AppError


class DatabaseError(AppError):
    """A database operation could not be completed."""

    status_code = 503
    code = "database_error"
    message = "The database request could not be completed."


class DatabaseNotConfiguredError(DatabaseError):
    """No ``DATABASE_URL`` is configured.

    A 500: the caller did nothing wrong and nothing they can change will fix
    it. Distinct from unavailability, which is transient.
    """

    status_code = 500
    code = "database_not_configured"
    message = "No database is configured for this service."


class DatabaseUnavailableError(DatabaseError):
    """The database could not be reached."""

    status_code = 503
    code = "database_unavailable"
    message = "The database is currently unavailable."
