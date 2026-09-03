"""Token usage and what it cost.

Two things kept deliberately apart: how many tokens a call used, which the
provider reports, and what those tokens cost, which only a configured price
list can say. Conflating them is how a system ends up confidently reporting
made-up money.

**No prices are shipped.** The table is empty until an operator configures one.
Inventing plausible per-million rates would produce numbers that look
authoritative and are wrong the moment a vendor changes them -- and wrong money
in a report is worse than absent money, because absent money is obviously
absent. An unpriced call is still fully accounted in tokens, and says so.

Money is :class:`~decimal.Decimal`, not float. Cost is summed across many calls
and compared between runs; binary floating point makes those sums depend on
order, which is exactly the determinism the roadmap asks for here.
"""

from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.exceptions import AppError
from app.llm.base import LLMUsage

TOKENS_PER_UNIT = Decimal(1_000_000)
"""Prices are quoted per million tokens, the industry convention."""

COST_PRECISION = Decimal("0.000001")
"""Rounded to the microdollar, so a total is reproducible rather than drifting."""


class PricingConfigurationError(AppError):
    """A price list entry could not be read.

    A 500 raised at construction, so a malformed price surfaces when the
    application is built rather than inside an accounting report weeks later.
    """

    status_code = 500
    code = "pricing_configuration_error"
    message = "The model pricing configuration is not valid."


class ModelPricing(BaseModel):
    """What one model charges, per million tokens."""

    model_config = ConfigDict(frozen=True)

    model: str = Field(min_length=1)
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)


class CostEstimate(BaseModel):
    """What one call used, and what that came to."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    input_cost: Decimal = Field(default=Decimal("0"), ge=0)
    output_cost: Decimal = Field(default=Decimal("0"), ge=0)
    usage_reported: bool = Field(
        default=False,
        description=(
            "False when the provider reported no usage. Distinct from a real "
            "zero, and the difference matters: one is a free call, the other "
            "is a blind spot."
        ),
    )
    priced: bool = Field(
        default=False,
        description="False when no price is configured for this model.",
    )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost(self) -> Decimal:
        return self.input_cost + self.output_cost


class PricingTable:
    """Model name to price. Empty unless an operator configured it."""

    def __init__(self, prices: Iterable[ModelPricing] = ()) -> None:
        self._prices: dict[str, ModelPricing] = {}
        for price in prices:
            if price.model in self._prices:
                raise PricingConfigurationError(
                    "A model may be priced only once.",
                    details={"model": price.model},
                )
            self._prices[price.model] = price

    def get(self, model: str) -> ModelPricing | None:
        return self._prices.get(model)

    def models(self) -> Sequence[str]:
        return tuple(sorted(self._prices))

    def __len__(self) -> int:
        return len(self._prices)

    @classmethod
    def from_entries(cls, entries: Iterable[str]) -> "PricingTable":
        """Parse ``model:input_per_million:output_per_million`` entries.

        The same comma-separated shape ``CORS_ORIGINS`` uses, for the same
        reason: it survives an environment variable without needing JSON.
        """
        return cls(_parse_entry(entry) for entry in entries if entry.strip())


def _parse_entry(entry: str) -> ModelPricing:
    parts = entry.rsplit(":", 2)
    if len(parts) != 3:
        raise PricingConfigurationError(
            "A price entry must be 'model:input_per_million:output_per_million'.",
            # The entry itself is configuration, not content, but only its
            # shape is quoted back.
            details={"fields": len(parts)},
        )
    model, raw_input, raw_output = (part.strip() for part in parts)
    try:
        pricing = ModelPricing(
            model=model,
            input_per_million=Decimal(raw_input),
            output_per_million=Decimal(raw_output),
        )
    except Exception as exc:
        raise PricingConfigurationError(
            "A price entry could not be read.",
            details={"model": model, "reason": type(exc).__name__},
        ) from None
    return pricing


def estimate_cost(
    *,
    provider: str,
    model: str,
    usage: LLMUsage | None,
    pricing: PricingTable,
) -> CostEstimate:
    """Price one call, reporting honestly when it cannot be priced.

    Missing usage is never an error. A provider that reports nothing, or a
    deterministic provider that has nothing to report, still produces a
    complete estimate -- with ``usage_reported`` false, so a reader can tell a
    blind spot from a free call.

    ``LLMUsage`` defaults to zeros, so a call reporting 0/0 is treated as
    unreported: a real completion always consumes at least some input tokens,
    and the alternative reading would quietly record every static-provider call
    as a genuine zero.
    """
    reported = usage is not None and (usage.input_tokens or usage.output_tokens)
    input_tokens = usage.input_tokens if usage else 0
    output_tokens = usage.output_tokens if usage else 0

    price = pricing.get(model)
    if price is None:
        return CostEstimate(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_reported=bool(reported),
            priced=False,
        )

    return CostEstimate(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_cost=_cost(input_tokens, price.input_per_million),
        output_cost=_cost(output_tokens, price.output_per_million),
        usage_reported=bool(reported),
        priced=True,
    )


def _cost(tokens: int, per_million: Decimal) -> Decimal:
    return (Decimal(tokens) / TOKENS_PER_UNIT * per_million).quantize(
        COST_PRECISION, rounding=ROUND_HALF_UP
    )


class UsageLedger:
    """Running totals across several calls.

    Addition is over the already-rounded per-call figures, so the total a
    caller sees is the sum of the numbers it was shown -- not a separately
    rounded quantity that disagrees with them.
    """

    def __init__(self) -> None:
        self._entries: list[CostEstimate] = []

    def add(self, estimate: CostEstimate) -> CostEstimate:
        self._entries.append(estimate)
        return estimate

    @property
    def entries(self) -> Sequence[CostEstimate]:
        return tuple(self._entries)

    @property
    def calls(self) -> int:
        return len(self._entries)

    @property
    def input_tokens(self) -> int:
        return sum(e.input_tokens for e in self._entries)

    @property
    def output_tokens(self) -> int:
        return sum(e.output_tokens for e in self._entries)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost(self) -> Decimal:
        return sum((e.total_cost for e in self._entries), Decimal("0"))

    @property
    def fully_priced(self) -> bool:
        """Whether every call could be priced.

        A total built from a mix of priced and unpriced calls understates the
        real figure, and a reader has to be told that rather than guess it.
        """
        return all(e.priced for e in self._entries)

    def by_model(self) -> Mapping[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for entry in self._entries:
            totals[entry.model] = totals.get(entry.model, Decimal("0")) + entry.total_cost
        return dict(sorted(totals.items()))

    def summary(self) -> str:
        """One line for a log. Counts and money only."""
        return (
            f"calls={self.calls} input_tokens={self.input_tokens} "
            f"output_tokens={self.output_tokens} cost={self.total_cost} "
            f"fully_priced={self.fully_priced}"
        )
