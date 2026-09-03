"""Rate-limit failures, expressed as application errors."""

from app.core.exceptions import AppError


class RateLimitExceededError(AppError):
    """The caller has asked for too much, too fast.

    Carries ``Retry-After`` so a client waits the right amount instead of
    guessing or hammering. The message names no backend, no window arithmetic
    and no other caller -- a limit is about this request, and everything else
    is somebody else's business.
    """

    status_code = 429
    code = "rate_limit_exceeded"
    message = "Too many requests. Please retry shortly."

    def __init__(self, retry_after_seconds: int = 1) -> None:
        # At least a second: a Retry-After of 0 invites an immediate retry,
        # which is the behaviour the limit exists to prevent.
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(details={"retry_after_seconds": self.retry_after_seconds})

    @property
    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after_seconds)}


class RateLimitConfigurationError(AppError):
    """The configured limiter cannot be built."""

    status_code = 500
    code = "rate_limit_configuration_error"
    message = "The rate limit configuration is not valid."
