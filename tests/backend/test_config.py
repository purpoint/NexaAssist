"""Settings: the CORS regression, and the total-timeout contract."""

from pathlib import Path

import pytest

from app.core.config import Settings

DOCUMENTED_FORM = "http://localhost:5173"


def write_env(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body)
    return path


# --------------------------------------------------------------------------
# CORS_ORIGINS regression
#
# pydantic-settings JSON-decodes complex-typed fields straight off the
# environment, before any validator runs. Without NoDecode the documented
# comma-separated form raised SettingsError and the setting was unusable from
# any external source -- `cp .env.example .env` crashed the app at startup.
# --------------------------------------------------------------------------


def test_cors_origins_loads_from_a_dotenv_file(tmp_path: Path) -> None:
    env = write_env(tmp_path, f"CORS_ORIGINS={DOCUMENTED_FORM}\n")

    settings = Settings(_env_file=str(env))

    assert settings.cors_origins == [DOCUMENTED_FORM]


def test_cors_origins_loads_from_an_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", DOCUMENTED_FORM)

    assert Settings().cors_origins == [DOCUMENTED_FORM]


def test_cors_origins_accepts_several_comma_separated_values(tmp_path: Path) -> None:
    env = write_env(
        tmp_path, "CORS_ORIGINS=http://localhost:5173, http://localhost:3000\n"
    )

    settings = Settings(_env_file=str(env))

    assert settings.cors_origins == ["http://localhost:5173", "http://localhost:3000"]


def test_cors_origins_ignores_blank_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test,,  ,http://b.test")

    assert Settings().cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_falls_back_to_its_default(tmp_path: Path) -> None:
    env = write_env(tmp_path, "APP_NAME=NexaAssist\n")

    assert Settings(_env_file=str(env)).cors_origins == [DOCUMENTED_FORM]


def test_the_shipped_env_example_loads_without_edits(tmp_path: Path) -> None:
    """`cp .env.example .env` must simply work."""
    example = Path(__file__).resolve().parents[2] / ".env.example"
    env = write_env(tmp_path, example.read_text())

    settings = Settings(_env_file=str(env))

    assert settings.cors_origins == [DOCUMENTED_FORM]
    assert settings.llm_provider == "groq"
    assert settings.llm_api_key is None  # the shipped file carries no key


# --------------------------------------------------------------------------
# Total timeout
# --------------------------------------------------------------------------


def test_total_timeout_defaults_above_a_single_attempt() -> None:
    settings = Settings()

    assert settings.llm_total_timeout_seconds >= settings.llm_timeout_seconds


def test_total_timeout_below_the_attempt_timeout_is_rejected() -> None:
    with pytest.raises(ValueError, match="LLM_TOTAL_TIMEOUT_SECONDS"):
        Settings(llm_timeout_seconds=30.0, llm_total_timeout_seconds=10.0)


def test_total_timeout_is_projected_into_the_provider_config() -> None:
    from app.llm.factory import config_from_settings

    settings = Settings(llm_timeout_seconds=5.0, llm_total_timeout_seconds=42.0)

    assert config_from_settings(settings).total_timeout_seconds == 42.0
