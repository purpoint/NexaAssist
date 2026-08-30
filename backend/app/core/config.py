"""Application settings.

Values come from the environment (or a local ``.env`` file) and are validated
once at import time. Nothing else in the codebase should read ``os.environ``
directly -- add the variable here instead, and document it in ``.env.example``.
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the backend service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

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


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
