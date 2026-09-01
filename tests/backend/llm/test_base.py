"""The provider abstraction: config projection, registry, and both providers."""

import pytest
from pydantic import BaseModel, SecretStr

from app.core.config import Settings
from app.llm.base import LLMConfig, LLMPrompt, LLMProvider, StructuredCompletion
from app.llm.errors import LLMError
from app.llm.factory import PROVIDER_NAMES, build_provider, config_from_settings
from app.llm.providers.groq_provider import (
    GroqProvider,
    build_client,
    strict_json_schema,
)
from app.llm.providers.static_provider import StaticLLMProvider


class Sample(BaseModel):
    """A stand-in schema, so these tests own no domain vocabulary."""

    answer: str
    score: float


def make_config(**overrides: object) -> LLMConfig:
    defaults: dict[str, object] = {
        "provider": "static",
        "model": "test-model",
        "api_key": SecretStr("test-key"),
    }
    return LLMConfig(**{**defaults, **overrides})


PROMPT = LLMPrompt(system="You are a test.", user="Say something.")


# --------------------------------------------------------------------------
# Protocol conformance
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    [
        StaticLLMProvider(make_config(provider="static")),
        GroqProvider(make_config(provider="groq")),
    ],
    ids=["static", "groq"],
)
def test_providers_satisfy_the_protocol(provider: object) -> None:
    assert isinstance(provider, LLMProvider)


def test_registry_and_settings_options_agree() -> None:
    """A provider is only usable if both the registry and Settings know it."""
    allowed = Settings.model_fields["llm_provider"].annotation
    assert set(PROVIDER_NAMES) == set(allowed.__args__)


# --------------------------------------------------------------------------
# Settings -> LLMConfig
# --------------------------------------------------------------------------


def test_config_is_projected_from_settings() -> None:
    settings = Settings(
        llm_provider="static",
        llm_model="some-model",
        llm_api_key=SecretStr("shh"),
        llm_timeout_seconds=12.5,
        llm_max_retries=3,
        llm_max_output_tokens=256,
        llm_temperature=0.2,
        llm_total_timeout_seconds=99.0,
    )

    config = config_from_settings(settings)

    assert config.provider == "static"
    assert config.model == "some-model"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "shh"
    assert config.timeout_seconds == 12.5
    assert config.max_retries == 3
    assert config.max_output_tokens == 256
    assert config.temperature == 0.2
    assert config.total_timeout_seconds == 99.0


def test_api_key_is_read_from_the_groq_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-from-env")

    settings = Settings()

    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "gsk-from-env"


def test_blank_environment_values_are_treated_as_unset() -> None:
    """`.env.example` ships these blank; blank must mean unset, not empty."""
    settings = Settings(llm_api_key="", llm_temperature="")

    assert settings.llm_api_key is None
    assert settings.llm_temperature is None


def test_temperature_is_unset_by_default() -> None:
    assert Settings().llm_temperature is None


def test_unknown_provider_is_rejected_by_settings() -> None:
    with pytest.raises(ValueError):
        Settings(llm_provider="not-a-provider")


def test_secret_does_not_leak_through_repr() -> None:
    settings = Settings(llm_api_key=SecretStr("gsk-super-secret"))

    assert "super-secret" not in repr(settings)
    assert "super-secret" not in repr(config_from_settings(settings))


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [("static", StaticLLMProvider), ("groq", GroqProvider)],
)
def test_build_provider_returns_the_registered_implementation(
    name: str, expected: type
) -> None:
    assert isinstance(build_provider(make_config(provider=name)), expected)


def test_build_provider_rejects_an_unknown_name() -> None:
    with pytest.raises(LLMError) as excinfo:
        build_provider(make_config(provider="nope"))

    assert excinfo.value.details == {
        "provider": "nope",
        "supported": list(PROVIDER_NAMES),
    }


# --------------------------------------------------------------------------
# Static provider
# --------------------------------------------------------------------------


def make_static() -> StaticLLMProvider:
    return StaticLLMProvider(
        make_config(), canned={Sample: Sample(answer="canned", score=1.0)}
    )


@pytest.mark.anyio
async def test_static_provider_returns_its_canned_response() -> None:
    completion = await make_static().complete_structured(prompt=PROMPT, schema=Sample)

    assert isinstance(completion, StructuredCompletion)
    assert completion.output == Sample(answer="canned", score=1.0)
    assert completion.provider == "static"
    assert completion.stop_reason == "end_turn"


@pytest.mark.anyio
async def test_static_provider_is_deterministic() -> None:
    provider = make_static()

    first = await provider.complete_structured(prompt=PROMPT, schema=Sample)
    second = await provider.complete_structured(prompt=PROMPT, schema=Sample)

    assert first.model_dump() == second.model_dump()


@pytest.mark.anyio
async def test_static_provider_hands_back_a_copy() -> None:
    """A caller mutating the result must not corrupt later calls."""
    provider = make_static()

    first = await provider.complete_structured(prompt=PROMPT, schema=Sample)
    first.output.answer = "mutated"
    second = await provider.complete_structured(prompt=PROMPT, schema=Sample)

    assert second.output.answer == "canned"


@pytest.mark.anyio
async def test_static_provider_refuses_to_invent_an_unregistered_schema() -> None:
    provider = StaticLLMProvider(make_config(), canned={})

    with pytest.raises(LLMError, match="no canned response"):
        await provider.complete_structured(prompt=PROMPT, schema=Sample)


# --------------------------------------------------------------------------
# Groq schema preparation (pure, offline)
# --------------------------------------------------------------------------


def test_strict_schema_closes_objects_and_requires_every_field() -> None:
    """Groq's strict mode demands both; Pydantic emits neither by default."""
    document = strict_json_schema(Sample)

    assert document["additionalProperties"] is False
    assert sorted(document["required"]) == ["answer", "score"]


def test_strict_schema_applies_to_nested_objects() -> None:
    class Outer(BaseModel):
        inner: Sample

    document = strict_json_schema(Outer)
    nested = document["$defs"]["Sample"]

    assert nested["additionalProperties"] is False
    assert sorted(nested["required"]) == ["answer", "score"]


# --------------------------------------------------------------------------
# Groq client construction (no requests are made)
# --------------------------------------------------------------------------


def test_client_receives_the_configured_key_timeout_and_retries() -> None:
    client = build_client(
        make_config(api_key=SecretStr("gsk-test"), timeout_seconds=7.5, max_retries=4)
    )

    assert client.api_key == "gsk-test"
    assert client.timeout == 7.5
    assert client.max_retries == 4


def test_client_omits_the_key_so_the_sdk_can_resolve_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset key must not become an empty one -- the SDK reads the env."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-from-env")

    client = build_client(make_config(api_key=None))

    assert client.api_key == "gsk-from-env"
