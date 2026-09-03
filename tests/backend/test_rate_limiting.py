"""Rate limiting: counting, refusing, resetting, and revealing nothing."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.v1.limits import enforce_rate_limit
from app.auth.identity import RequestIdentity
from app.core.config import Settings
from app.main import create_app
from app.ratelimit.base import RateLimitDecision, RateLimiter
from app.ratelimit.errors import RateLimitConfigurationError, RateLimitExceededError
from app.ratelimit.factory import LIMITER_NAMES, build_rate_limiter, get_rate_limiter
from app.ratelimit.limiters import InMemoryRateLimiter, NullRateLimiter
from app.routing.router import RouteReason
from app.schemas.intent import IntentCategory
from app.services.assistant import AssistantReply

ASSISTANT = "/api/v1/assistant/messages"


class Clock:
    """A clock a test moves by hand, so no window test has to sleep."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def limiter(limit: int = 2, window: int = 60) -> tuple[InMemoryRateLimiter, Clock]:
    clock = Clock()
    return InMemoryRateLimiter(limit=limit, window_seconds=window, clock=clock), clock


# --------------------------------------------------------------------------
# Counting


@pytest.mark.anyio
async def test_requests_under_the_limit_are_allowed() -> None:
    made, _ = limiter(limit=3)
    decisions = [await made.check("web") for _ in range(3)]
    assert [d.allowed for d in decisions] == [True, True, True]
    assert [d.remaining for d in decisions] == [2, 1, 0]


@pytest.mark.anyio
async def test_the_request_after_the_limit_is_refused() -> None:
    made, _ = limiter(limit=2)
    for _ in range(2):
        await made.check("web")
    refused = await made.check("web")
    assert refused.allowed is False
    assert refused.remaining == 0
    assert refused.retry_after_seconds > 0


@pytest.mark.anyio
async def test_checking_consumes_the_request() -> None:
    """A free check would let a caller poll the limiter for nothing."""
    made, _ = limiter(limit=1)
    assert (await made.check("web")).allowed is True
    assert (await made.check("web")).allowed is False


@pytest.mark.anyio
async def test_keys_are_counted_separately() -> None:
    made, _ = limiter(limit=1)
    assert (await made.check("web")).allowed is True
    assert (await made.check("worker")).allowed is True
    assert (await made.check("web")).allowed is False


@pytest.mark.anyio
async def test_the_window_resets() -> None:
    made, clock = limiter(limit=1, window=60)
    assert (await made.check("web")).allowed is True
    assert (await made.check("web")).allowed is False

    clock.now = 60.0
    assert (await made.check("web")).allowed is True


@pytest.mark.anyio
async def test_the_window_does_not_reset_early() -> None:
    made, clock = limiter(limit=1, window=60)
    await made.check("web")
    clock.now = 59.9
    assert (await made.check("web")).allowed is False


@pytest.mark.anyio
async def test_retry_after_counts_down_within_the_window() -> None:
    made, clock = limiter(limit=1, window=60)
    await made.check("web")
    early = await made.check("web")
    clock.now = 50.0
    late = await made.check("web")
    assert early.retry_after_seconds > late.retry_after_seconds
    assert late.retry_after_seconds >= 1, "never zero, which invites an instant retry"


@pytest.mark.anyio
async def test_expired_windows_are_pruned() -> None:
    """Otherwise the counter grows once per key per window, forever."""
    made, clock = limiter(limit=5, window=10)
    for window in range(4):
        clock.now = window * 10.0
        await made.check(f"key-{window}")
    assert len(made._counts) == 1


@pytest.mark.anyio
async def test_concurrent_requests_are_all_counted() -> None:
    """Six overlapping requests against a limit of two: two get through."""
    made, _ = limiter(limit=2)
    decisions = await asyncio.gather(*(made.check("web") for _ in range(6)))
    assert sum(1 for d in decisions if d.allowed) == 2


# --------------------------------------------------------------------------
# Disabled mode


@pytest.mark.anyio
async def test_the_null_limiter_allows_everything() -> None:
    made = NullRateLimiter()
    assert made.enforces is False
    for _ in range(100):
        assert (await made.check("web")).allowed is True


def test_the_default_enforces_nothing() -> None:
    """A service already behind a limiting gateway should not limit twice."""
    assert isinstance(build_rate_limiter(Settings()), NullRateLimiter)


# --------------------------------------------------------------------------
# Configuration


def test_every_limiter_satisfies_the_protocol() -> None:
    assert isinstance(NullRateLimiter(), RateLimiter)
    assert isinstance(limiter()[0], RateLimiter)


def test_the_registry_matches_the_setting() -> None:
    allowed = Settings.model_fields["rate_limit_provider"].annotation
    assert set(LIMITER_NAMES) == set(allowed.__args__)


def test_the_configured_backend_is_built() -> None:
    built = build_rate_limiter(
        Settings(rate_limit_provider="memory", rate_limit_requests=5)
    )
    assert isinstance(built, InMemoryRateLimiter)
    assert built._limit == 5


def test_redis_limiting_without_a_url_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(rate_limit_provider="redis")


@pytest.mark.parametrize(("limit", "window"), [(0, 60), (-1, 60), (5, 0)])
def test_a_nonsensical_limit_is_rejected(limit: int, window: int) -> None:
    with pytest.raises(RateLimitConfigurationError):
        InMemoryRateLimiter(limit=limit, window_seconds=window)


def test_the_limiter_is_a_single_shared_instance() -> None:
    """A fresh one per request would count every request as the first."""
    assert get_rate_limiter() is get_rate_limiter()


# --------------------------------------------------------------------------
# The error a client sees


def test_the_error_carries_retry_after() -> None:
    error = RateLimitExceededError(17)
    assert error.status_code == 429
    assert error.headers["Retry-After"] == "17"
    assert error.details == {"retry_after_seconds": 17}


def test_retry_after_is_never_zero() -> None:
    assert RateLimitExceededError(0).retry_after_seconds == 1


def test_the_error_reveals_no_backend_or_other_callers() -> None:
    rendered = RateLimitExceededError(5).to_response().model_dump_json()
    for leak in ("redis", "localhost", "window", "counter", "subject", "6379"):
        assert leak not in rendered.lower()


# --------------------------------------------------------------------------
# Over HTTP


class StubAssistant:
    async def respond(self, message: str, **kwargs: object) -> AssistantReply:
        return AssistantReply(
            reply="a reply",
            intent=IntentCategory.OTHER,
            confidence=0.0,
            handler="fallback",
            route_reason=RouteReason.NO_CATEGORY,
            fallback=True,
            handled=False,
        )


def limited_client(limit: int = 2) -> TestClient:
    from app.api.v1.assistant import get_assistant_service

    settings = Settings(rate_limit_provider="memory", rate_limit_requests=limit)
    app = create_app(settings)
    app.dependency_overrides[get_assistant_service] = StubAssistant
    # One instance, returned every time. A fresh limiter per request would
    # count every request as the first -- which is exactly what a limiter must
    # not do, and what the production dependency caches to avoid.
    shared = build_rate_limiter(settings)
    app.dependency_overrides[get_rate_limiter] = lambda: shared
    return TestClient(app)


def test_requests_are_refused_once_over_the_limit() -> None:
    with limited_client(limit=2) as client:
        codes = [
            client.post(ASSISTANT, json={"message": "hi"}).status_code
            for _ in range(3)
        ]
    assert codes == [200, 200, 429]


def test_the_refusal_tells_the_client_when_to_retry() -> None:
    with limited_client(limit=1) as client:
        client.post(ASSISTANT, json={"message": "hi"})
        refused = client.post(ASSISTANT, json={"message": "hi"})
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) >= 1
    assert refused.json()["code"] == "rate_limit_exceeded"


def test_the_refusal_body_leaks_nothing() -> None:
    with limited_client(limit=1) as client:
        client.post(ASSISTANT, json={"message": "hi"})
        body = client.post(ASSISTANT, json={"message": "hi"}).text
    assert "redis" not in body.lower() and "localhost" not in body


def test_health_is_never_rate_limited() -> None:
    """A probe that can be throttled stops being a probe."""
    with limited_client(limit=1) as client:
        client.post(ASSISTANT, json={"message": "hi"})
        client.post(ASSISTANT, json={"message": "hi"})
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/ready").status_code in (200, 503)


def test_with_limiting_disabled_nothing_is_refused() -> None:
    """The pre-M19-C3 contract, still intact."""
    from app.api.v1.assistant import get_assistant_service

    app = create_app()
    app.dependency_overrides[get_assistant_service] = StubAssistant
    with TestClient(app) as client:
        codes = [
            client.post(ASSISTANT, json={"message": "hi"}).status_code
            for _ in range(20)
        ]
    assert set(codes) == {200}


@pytest.mark.anyio
async def test_the_dependency_returns_the_identity_it_checked() -> None:
    """So a route depends on one thing and gets both."""
    identity = RequestIdentity.api_key("web")
    returned = await enforce_rate_limit(identity, NullRateLimiter())
    assert returned is identity


@pytest.mark.anyio
async def test_the_dependency_raises_when_over_the_limit() -> None:
    made, _ = limiter(limit=1)
    identity = RequestIdentity.api_key("web")
    await enforce_rate_limit(identity, made)
    with pytest.raises(RateLimitExceededError):
        await enforce_rate_limit(identity, made)


def test_a_decision_is_frozen() -> None:
    with pytest.raises(Exception):
        RateLimitDecision(allowed=True).allowed = False  # type: ignore[misc]
