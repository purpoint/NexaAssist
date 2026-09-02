"""Application settings.

Values come from the environment (or a local ``.env`` file) and are validated
once at import time. Nothing else in the codebase should read ``os.environ``
directly -- add the variable here instead, and document it in ``.env.example``.
"""

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the backend service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # So tests and callers can construct Settings by field name even where
        # a field reads a provider-specific environment variable.
        populate_by_name=True,
    )

    app_name: str = Field(default="NexaAssist")
    app_env: str = Field(default="local")
    debug: bool = Field(default=True)

    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000)
    api_prefix: str = Field(default="/api")

    # Pre-v1 alias for the health endpoint. Deprecated; see docs/architecture.md.
    enable_legacy_health_route: bool = Field(default=True)

    log_level: str = Field(default="INFO")

    # ``NoDecode`` is load-bearing, not decoration. Without it
    # pydantic-settings JSON-decodes complex-typed fields straight off the
    # environment or .env file, before any validator runs -- so the documented
    # comma-separated form raises SettingsError and the setting is unusable
    # from any external source. Scoped to this field on purpose: disabling
    # decoding globally would silently change behaviour for future settings.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    llm_provider: Literal["groq", "static"] = Field(default="groq")
    # Structured outputs are model-dependent on Groq; see docs/architecture.md
    # for the models that support them.
    llm_model: str = Field(default="openai/gpt-oss-120b")
    # Read from GROQ_API_KEY -- the same variable the Groq SDK reads, so an
    # already-exported key needs no .env entry. SecretStr keeps the value out
    # of reprs, logs, and error bodies.
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GROQ_API_KEY"),
    )
    # Per attempt, not for the whole call: worst-case wall clock for one
    # request is llm_timeout_seconds * (llm_max_retries + 1).
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=1, ge=0)
    llm_max_output_tokens: int = Field(default=4096, gt=0)
    # Ceiling for one complete provider call. Must exceed the per-attempt
    # timeout, because retry backoff sleeps count against it -- a real call
    # was observed taking 34.8s after a server-directed retry-after.
    llm_total_timeout_seconds: float = Field(default=90.0, gt=0)

    # May carry a password in any non-local environment, so it is a secret
    # and is registered with the log redactor. Unset means "no database":
    # the service still runs for work that does not need one.
    database_url: SecretStr | None = Field(default=None)
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    # Echoes SQL *and* bound parameters, which can include customer data.
    db_echo: bool = Field(default=False)

    # Which embedder to use. "fastembed" runs a local ONNX model; "hashing" is
    # deterministic and offline, and is what the test suite uses so the suite
    # never downloads a model or reaches the network.
    embedding_provider: Literal["fastembed", "hashing"] = Field(default="fastembed")
    # How many chunks a retrieval returns before the answer is composed.
    retrieval_top_k: int = Field(default=4, ge=1, le=20)

    # What one agent run may spend. Two limits because a loop can burn steps
    # without calling a tool, and can call tools more often than it takes steps.
    agent_max_steps: int = Field(default=6, ge=1, le=50)
    agent_max_tool_calls: int = Field(default=8, ge=0, le=100)

    # Which job queue backend to use. "memory" is in-process, deterministic,
    # and offline -- the default, and what the test suite runs on. It is not
    # durable and is not shared between processes. "redis" is durable and
    # shared, and needs REDIS_URL.
    job_queue: Literal["memory", "redis"] = Field(default="memory")
    # May carry a password, so it is a secret for the same reason DATABASE_URL
    # is. Unset is the normal case: the default backend needs no server.
    redis_url: SecretStr | None = Field(default=None)
    # Keys are namespaced so one Redis server can safely host more than one
    # application. Nothing outside this prefix is ever read or written.
    redis_namespace: str = Field(default="nexaassist:jobs", min_length=1)
    # How many times a job may be dequeued before it is dead-lettered rather
    # than retried. Counts attempts, not retries: 1 means "never retry".
    job_max_attempts: int = Field(default=3, ge=1, le=20)

    # Below this self-reported confidence a classification is treated as
    # ambiguous and sent to the fallback instead of a specialised handler.
    # Self-reported, not calibrated -- see docs/prompt.md -- so this is a
    # coarse guard, not a probability threshold.
    routing_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    llm_temperature: float | None = Field(default=None, ge=0.0, le=1.0)

    @property
    def api_v1_prefix(self) -> str:
        """Path prefix for version 1 routes, e.g. ``/api/v1``.

        Derived here so no other module hardcodes the version segment.
        """
        return f"{self.api_prefix.rstrip('/')}/v1"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, as written in ``.env.example``."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator(
        "llm_api_key", "llm_temperature", "database_url", "redis_url", mode="before"
    )
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        """Treat a blank environment value as unset.

        ``.env.example`` ships these keys with empty values, and a copied
        ``.env`` would otherwise yield an empty secret rather than ``None``,
        which would stop the SDK resolving credentials on its own.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _require_async_database_driver(self) -> Self:
        """Reject a synchronous driver URL before it reaches the engine.

        ``postgresql://`` against an async engine fails at connect time with a
        message about a missing greenlet, which points nowhere useful. Only the
        scheme is quoted back -- the rest of the URL may hold a password.
        """
        if self.database_url is None:
            return self
        url = self.database_url.get_secret_value()
        scheme = url.split("://", 1)[0]
        if scheme != "postgresql+asyncpg":
            raise ValueError(
                "DATABASE_URL must use the postgresql+asyncpg driver, "
                f"got {scheme!r}."
            )
        return self

    @model_validator(mode="after")
    def _redis_backend_needs_a_url(self) -> Self:
        """Fail at startup rather than on the first job.

        Selecting the redis backend without a URL is a misconfiguration, and
        the useful moment to say so is while the operator is still looking at
        the deploy -- not hours later when something tries to enqueue.
        """
        if self.job_queue == "redis" and self.redis_url is None:
            raise ValueError("JOB_QUEUE=redis requires REDIS_URL to be set.")
        return self

    @model_validator(mode="after")
    def _total_timeout_covers_one_attempt(self) -> Self:
        """A total budget smaller than a single attempt could never be met."""
        if self.llm_total_timeout_seconds < self.llm_timeout_seconds:
            raise ValueError(
                "LLM_TOTAL_TIMEOUT_SECONDS must be at least LLM_TIMEOUT_SECONDS "
                f"({self.llm_total_timeout_seconds} < {self.llm_timeout_seconds})."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
