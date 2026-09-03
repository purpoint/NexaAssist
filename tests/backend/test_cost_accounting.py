"""Token accounting and deterministic cost calculation."""

from decimal import Decimal

import pytest

from app.core.config import Settings
from app.llm.base import LLMUsage
from app.observability.cost import (
    CostEstimate,
    ModelPricing,
    PricingConfigurationError,
    PricingTable,
    UsageLedger,
    estimate_cost,
)
from app.observability.factory import build_pricing_table

MODEL = "openai/gpt-oss-120b"


@pytest.fixture
def pricing() -> PricingTable:
    return PricingTable([
        ModelPricing(
            model=MODEL,
            input_per_million=Decimal("0.15"),
            output_per_million=Decimal("0.60"),
        )
    ])


def priced(usage: LLMUsage | None, table: PricingTable) -> CostEstimate:
    return estimate_cost(provider="groq", model=MODEL, usage=usage, pricing=table)


# --------------------------------------------------------------------------
# Nothing is priced by default


def test_no_prices_ship_by_default() -> None:
    """An invented rate looks authoritative and is wrong."""
    assert len(build_pricing_table(Settings())) == 0


def test_an_unpriced_model_still_accounts_tokens(pricing: PricingTable) -> None:
    estimate = estimate_cost(
        provider="groq",
        model="some-other-model",
        usage=LLMUsage(input_tokens=1000, output_tokens=500),
        pricing=pricing,
    )
    assert estimate.priced is False
    assert estimate.total_cost == Decimal("0")
    assert estimate.total_tokens == 1500
    assert estimate.usage_reported is True


# --------------------------------------------------------------------------
# Calculation


def test_cost_is_computed_per_million_tokens(pricing: PricingTable) -> None:
    estimate = priced(LLMUsage(input_tokens=1_000_000, output_tokens=1_000_000), pricing)
    assert estimate.input_cost == Decimal("0.150000")
    assert estimate.output_cost == Decimal("0.600000")
    assert estimate.total_cost == Decimal("0.750000")


def test_input_and_output_are_priced_separately(pricing: PricingTable) -> None:
    """Output usually costs more; one blended rate would understate it."""
    estimate = priced(LLMUsage(input_tokens=1000, output_tokens=1000), pricing)
    assert estimate.output_cost > estimate.input_cost


def test_calculation_is_deterministic(pricing: PricingTable) -> None:
    usage = LLMUsage(input_tokens=1234, output_tokens=567)
    assert priced(usage, pricing) == priced(usage, pricing)


def test_money_is_decimal_not_float(pricing: PricingTable) -> None:
    """Costs are summed and compared; float sums depend on order."""
    estimate = priced(LLMUsage(input_tokens=1, output_tokens=1), pricing)
    assert isinstance(estimate.total_cost, Decimal)


def test_totals_are_order_independent(pricing: PricingTable) -> None:
    amounts = [
        priced(LLMUsage(input_tokens=n, output_tokens=n), pricing).total_cost
        for n in (1, 3, 7, 11, 13)
    ]
    assert sum(amounts) == sum(reversed(amounts))


def test_cost_is_rounded_to_the_microdollar(pricing: PricingTable) -> None:
    estimate = priced(LLMUsage(input_tokens=1, output_tokens=0), pricing)
    assert estimate.input_cost == Decimal("0.000000")
    assert estimate.input_cost.as_tuple().exponent == -6


# --------------------------------------------------------------------------
# Missing usage is not an error


def test_absent_usage_does_not_crash(pricing: PricingTable) -> None:
    estimate = priced(None, pricing)
    assert estimate.usage_reported is False
    assert estimate.total_tokens == 0
    assert estimate.total_cost == Decimal("0")


def test_zero_usage_reads_as_unreported(pricing: PricingTable) -> None:
    """A real completion always consumes some input tokens.

    StaticLLMProvider returns LLMUsage() -- all zeros -- and recording that as
    a genuine free call would quietly report a blind spot as a fact.
    """
    assert priced(LLMUsage(), pricing).usage_reported is False


def test_a_reported_call_is_distinguishable_from_a_blind_spot(
    pricing: PricingTable,
) -> None:
    assert priced(LLMUsage(input_tokens=1), pricing).usage_reported is True


def test_output_only_usage_counts_as_reported(pricing: PricingTable) -> None:
    assert priced(LLMUsage(output_tokens=5), pricing).usage_reported is True


# --------------------------------------------------------------------------
# Configuration


def test_entries_parse_from_the_comma_separated_form() -> None:
    table = build_pricing_table(
        Settings(llm_pricing=f"{MODEL}:0.15:0.60,other-model:1:2")
    )
    assert table.models() == (MODEL, "other-model")
    assert table.get("other-model").output_per_million == Decimal("2")


def test_a_model_name_may_contain_colons() -> None:
    """Split from the right: provider-qualified names carry separators."""
    table = PricingTable.from_entries(["vendor:family:model:0.1:0.2"])
    assert table.models() == ("vendor:family:model",)


def test_blank_entries_are_ignored() -> None:
    assert len(PricingTable.from_entries(["", "  "])) == 0


@pytest.mark.parametrize("entry", ["model:0.1", "model", "model:abc:0.2", "model:-1:2"])
def test_a_malformed_entry_is_rejected_at_construction(entry: str) -> None:
    with pytest.raises(PricingConfigurationError):
        PricingTable.from_entries([entry])


def test_a_duplicate_model_is_rejected() -> None:
    with pytest.raises(PricingConfigurationError):
        PricingTable.from_entries([f"{MODEL}:1:2", f"{MODEL}:3:4"])


def test_the_pricing_error_quotes_no_secret() -> None:
    with pytest.raises(PricingConfigurationError) as caught:
        PricingTable.from_entries(["model:abc:0.2"])
    assert "abc" not in caught.value.to_response().model_dump_json()


# --------------------------------------------------------------------------
# The ledger


def test_a_ledger_totals_tokens_and_cost(pricing: PricingTable) -> None:
    ledger = UsageLedger()
    ledger.add(priced(LLMUsage(input_tokens=1_000_000, output_tokens=0), pricing))
    ledger.add(priced(LLMUsage(input_tokens=0, output_tokens=1_000_000), pricing))

    assert ledger.calls == 2
    assert ledger.input_tokens == 1_000_000
    assert ledger.output_tokens == 1_000_000
    assert ledger.total_tokens == 2_000_000
    assert ledger.total_cost == Decimal("0.750000")


def test_the_total_is_the_sum_of_what_the_caller_was_shown(
    pricing: PricingTable,
) -> None:
    ledger = UsageLedger()
    shown = [
        ledger.add(priced(LLMUsage(input_tokens=n, output_tokens=n), pricing)).total_cost
        for n in (7, 13, 29)
    ]
    assert ledger.total_cost == sum(shown)


def test_a_ledger_reports_when_it_is_not_fully_priced(pricing: PricingTable) -> None:
    """A mixed total understates the real figure, and must say so."""
    ledger = UsageLedger()
    ledger.add(priced(LLMUsage(input_tokens=10), pricing))
    assert ledger.fully_priced is True
    ledger.add(
        estimate_cost(
            provider="groq",
            model="unpriced",
            usage=LLMUsage(input_tokens=10),
            pricing=pricing,
        )
    )
    assert ledger.fully_priced is False


def test_an_empty_ledger_is_vacuously_fully_priced() -> None:
    ledger = UsageLedger()
    assert ledger.fully_priced is True
    assert ledger.total_cost == Decimal("0")
    assert ledger.calls == 0


def test_a_ledger_breaks_down_by_model(pricing: PricingTable) -> None:
    ledger = UsageLedger()
    ledger.add(priced(LLMUsage(input_tokens=1_000_000), pricing))
    ledger.add(
        estimate_cost(
            provider="groq", model="other", usage=LLMUsage(input_tokens=5), pricing=pricing
        )
    )
    assert ledger.by_model() == {MODEL: Decimal("0.150000"), "other": Decimal("0")}


def test_the_ledger_summary_carries_counts_and_money_only(
    pricing: PricingTable,
) -> None:
    ledger = UsageLedger()
    ledger.add(priced(LLMUsage(input_tokens=1_000_000), pricing))
    assert ledger.summary() == (
        "calls=1 input_tokens=1000000 output_tokens=0 cost=0.150000 fully_priced=True"
    )


# --------------------------------------------------------------------------
# Existing behaviour is untouched


def test_the_provider_protocol_was_not_changed() -> None:
    """Accounting reads LLMUsage, which M2 already returned."""
    from app.llm.base import StructuredCompletion

    assert "usage" in StructuredCompletion.model_fields
    assert set(LLMUsage.model_fields) == {"input_tokens", "output_tokens"}
