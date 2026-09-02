"""Realtime failures, expressed as application errors."""

from app.core.exceptions import AppError


class RealtimeCapacityError(AppError):
    """The server is already holding as many connections as it will.

    A 503: nothing the client sent is wrong, and trying later may well work.
    """

    status_code = 503
    code = "realtime_at_capacity"
    message = "The service is not accepting new realtime connections."


class RealtimeProtocolError(AppError):
    """A frame did not match the wire contract.

    A 400 for consistency with the rest of the error taxonomy, though on a
    socket it is reported in an error frame rather than a status line.
    """

    status_code = 400
    code = "realtime_protocol_error"
    message = "The message did not match the expected format."
