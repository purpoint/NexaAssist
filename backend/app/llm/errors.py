"""Language-model failures, expressed as application errors.

Only the base class exists today. Every provider failure -- timeout, rate
limit, bad credentials, malformed output -- currently surfaces as this single
error, which is honest about how little the layer distinguishes so far. The
precise taxonomy and its per-failure status codes arrive with the
error-handling hardening step of M2; see ``docs/milestones.md``.

Because it subclasses :class:`~app.core.exceptions.AppError`, the handlers
registered in M1 already render it as an ``ErrorResponse`` -- ``app.core``
needs no change.
"""

from app.core.exceptions import AppError


class LLMError(AppError):
    """A language-model request could not be completed."""

    status_code = 500
    code = "llm_error"
    message = "The language model request could not be completed."
