"""Language-model failures, expressed as application errors.

Each class fixes three things: the HTTP status a caller sees, a stable
machine-readable ``code``, and a **generic** message. Provider SDK text never
appears in any of them -- a client learns what category of thing went wrong and
nothing about our vendor, our credentials, or our request shape.

The governing rule is that upstream status codes are not our status codes. A
provider returning 401 means *we* misconfigured a key; the caller authenticated
perfectly well, so returning 401 to them would send them debugging their own
credentials. That case is a 500. Only "try again later" conditions -- rate
limiting, provider outage, timeout -- pass a retryable status through.

Every subclass inherits :class:`~app.core.exceptions.AppError`, so the handlers
registered in M1 render them as ``ErrorResponse`` without further wiring.
"""

import math
from typing import Any

from app.core.exceptions import AppError


class LLMError(AppError):
    """A language-model request could not be completed.

    Also the catch-all for failures that do not fit a more precise subclass.
    """

    status_code = 500
    code = "llm_error"
    message = "The language model request could not be completed."


class LLMConfigurationError(LLMError):
    """The provider is misconfigured: missing key, wrong model, no access.

    Deliberately a 500. The caller did nothing wrong, and nothing they can
    change will fix it.
    """

    status_code = 500
    code = "llm_configuration_error"
    message = "The language model provider is not configured correctly."


class LLMRateLimitError(LLMError):
    """The provider is rate limiting us.

    Passed through as 429 because "try again later" is genuinely true for the
    caller, and carries ``Retry-After`` when the provider told us how long.
    """

    status_code = 429
    code = "llm_rate_limited"
    message = "The language model provider is rate limiting requests."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        merged = dict(details) if isinstance(details, dict) else details
        if retry_after_seconds is not None and isinstance(merged, dict):
            merged["retry_after_seconds"] = retry_after_seconds
        super().__init__(message, details=merged)
        self.retry_after_seconds = retry_after_seconds

    @property
    def headers(self) -> dict[str, str] | None:
        """Emit a real ``Retry-After`` header when the provider supplied one."""
        if self.retry_after_seconds is None:
            return None
        return {"Retry-After": str(max(0, math.ceil(self.retry_after_seconds)))}


class LLMTimeoutError(LLMError):
    """The operation exceeded its deadline, retries and backoff included."""

    status_code = 504
    code = "llm_timeout"
    message = "The language model request timed out."


class LLMUnavailableError(LLMError):
    """The provider could not be reached, or returned a server-side failure."""

    status_code = 503
    code = "llm_unavailable"
    message = "The language model provider is currently unavailable."


class LLMRequestError(LLMError):
    """The provider rejected the request we built.

    Our bug, not the caller's -- a malformed schema or an unsupported
    parameter -- so it surfaces as a 500 rather than a 400.
    """

    status_code = 500
    code = "llm_request_error"
    message = "The language model request was rejected by the provider."


class LLMInvalidOutputError(LLMError):
    """The model returned nothing usable, or output that failed validation.

    Raised rather than repaired or defaulted: a fabricated result that looks
    real is worse than a loud failure.
    """

    status_code = 500
    code = "llm_invalid_output"
    message = "The language model returned an unusable response."
