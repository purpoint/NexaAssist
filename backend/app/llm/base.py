"""Provider-agnostic types for the LLM layer.

Deliberately dependency-free: this module imports nothing from the rest of the
application. Everything here describes *what* a language-model call looks like,
never *how* a particular vendor performs it, so a second provider can be added
without touching a caller.
"""

from typing import Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, SecretStr

T = TypeVar("T", bound=BaseModel)


class LLMConfig(BaseModel):
    """The settings a provider needs, and nothing else.

    Narrow on purpose: providers never receive the application ``Settings``
    object, so a provider unit test constructs one of these directly instead of
    populating an environment.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=1, ge=0)
    # Ceiling for one complete call. Distinct from ``timeout_seconds``, which
    # bounds a single attempt: a provider that retries also sleeps between
    # attempts, and that sleep counts against this budget.
    total_timeout_seconds: float = Field(default=90.0, gt=0)
    max_output_tokens: int = Field(default=4096, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)


class LLMPrompt(BaseModel):
    """A single-turn prompt: one system instruction, one user turn.

    That is all any current caller needs. Widening this to a message list is
    additive once a multi-turn caller exists; inventing the list now would be
    structure without a user.
    """

    model_config = ConfigDict(frozen=True)

    system: str
    user: str


class LLMUsage(BaseModel):
    """Token accounting for a single call."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0


class StructuredCompletion(BaseModel, Generic[T]):
    """A schema-validated response, plus metadata about producing it.

    The metadata is not decoration: a later agent loop needs ``usage`` for
    budget accounting, and ``stop_reason`` to tell a finished answer from a
    truncated one.
    """

    output: T
    provider: str
    model: str
    stop_reason: str | None = None
    usage: LLMUsage = Field(default_factory=LLMUsage)
    latency_ms: float = 0.0


@runtime_checkable
class LLMProvider(Protocol):
    """What every language-model backend must offer.

    A ``Protocol`` rather than a base class: implementations neither import nor
    inherit from this type, so a test double or a future third-party adapter
    satisfies the contract structurally.
    """

    name: str

    async def complete_structured(
        self,
        *,
        prompt: LLMPrompt,
        schema: type[T],
        config: LLMConfig | None = None,
    ) -> StructuredCompletion[T]:
        """Return an instance of ``schema`` produced by the model.

        Implementations must return or raise within
        ``config.total_timeout_seconds``, counting any internal retrying they
        do. How that bound is achieved is the implementation's business; a
        caller may rely only on the bound itself.

        Failures are raised as :class:`~app.llm.errors.LLMError` or a subclass.
        No provider-specific exception may escape this method.
        """
        ...
