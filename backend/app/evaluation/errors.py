"""Evaluation failures, expressed as application errors."""

from app.core.exceptions import AppError


class EvaluationDefinitionError(AppError):
    """A suite, case, or check is malformed.

    A 500: it is a mistake in how the evaluation was written, not in anything
    a caller sent. Raised at construction so it surfaces when the suite is
    defined rather than midway through a run.
    """

    status_code = 500
    code = "evaluation_definition_error"
    message = "The evaluation is not valid."
