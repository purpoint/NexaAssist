"""The regression suite for model-facing changes.

Two distinct guards, because they catch two different things.

The **shipped suites** run the deterministic layers — policy and escalation —
where a different input genuinely produces a different expected output, so a
green suite means something. They must pass completely: a tolerance below 100%
on a deterministic target is a licence for a real break to hide under it.

The **prompt digests** are the model-facing guard. Prompt text cannot change
without its version changing, because a version that no longer identifies the
text it names makes every log line attributing output to a prompt version a
lie. This is what a suite over a canned provider could never catch.

What is deliberately absent: an offline suite over intent classification or
grounded answers. ``StaticLLMProvider`` returns one canned response per schema
regardless of input, so such a suite would be every case expecting the same
answer and would pass whatever the prompt said. Judging the model needs a real
provider and a live key — an operator action, not a test.
"""

import hashlib

import pytest

from app.evaluation.results import EvalReport
from app.evaluation.runner import compare_reports
from app.evaluation.suites import (
    ESCALATION_SUITE,
    POLICY_SUITE,
    default_plans,
    run_all,
    run_plan,
)
from app.llm.prompts import ALL_PROMPTS

pytestmark = pytest.mark.anyio


PINNED_PROMPT_DIGESTS = {
    "intent/v1": "9912d950c942d6e8",
    "grounded-answer/v1": "1918f061590218e7",
    "agent/v1": "f7f9d3fbd272df4b",
    "realtime-reply/v1": "a6429c7140a94e84",
}
"""Version -> first 16 hex of the SHA-256 of the prompt text.

Changing a prompt fails this test. That is the point, and the fix is to bump
the version and repin here in the same commit -- deliberately, and visibly in
the diff, rather than silently.
"""


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# The shipped suites


async def test_every_shipped_suite_passes_completely() -> None:
    """No tolerance: these targets are deterministic.

    A threshold below 100% here would let a genuine break hide under the
    allowance until somebody wondered why the number had always been 0.9.
    """
    for report in await run_all():
        assert report.failed == 0, report.describe_failures()
        assert report.pass_rate == 1.0


async def test_each_suite_actually_ran_its_cases() -> None:
    """A suite that silently stopped running is the failure this catches."""
    reports = {report.suite: report for report in await run_all()}
    assert reports["policy"].total == len(POLICY_SUITE)
    assert reports["escalation"].total == len(ESCALATION_SUITE)
    assert reports["policy"].total >= 4
    assert reports["escalation"].total >= 4


def outcomes(report: EvalReport) -> list[tuple[str, bool, tuple[tuple[str, str], ...]]]:
    """The semantic content of a report, without the timings.

    Durations differ between runs by definition, so whole-report equality could
    never hold. What must be identical is every verdict.
    """
    return [
        (
            result.case_id,
            result.passed,
            tuple((check.check, check.outcome.value) for check in result.checks),
        )
        for result in report.results
    ]


async def test_a_rerun_produces_identical_verdicts() -> None:
    """Determinism is the property the whole suite rests on."""
    for plan in default_plans():
        first = await run_plan(plan)
        second = await run_plan(plan)
        assert outcomes(first) == outcomes(second)
        assert compare_reports(first, second).clean


async def test_the_comparison_finds_nothing_between_two_clean_runs() -> None:
    plan = default_plans()[0]
    summary = compare_reports(await run_plan(plan), await run_plan(plan))
    assert summary.regressed == ()
    assert summary.removed == ()


async def test_the_policy_suite_covers_each_shipped_rule() -> None:
    """A rule with no case is a rule nothing would notice breaking."""
    tags = {tag for case in POLICY_SUITE.cases for tag in case.tags}
    assert {"financial", "complaint", "unresolved"} <= tags


async def test_the_escalation_suite_covers_both_verdicts() -> None:
    """A suite where everything escalates would pass a criteria that always did."""
    reports = {report.suite: report for report in await run_all()}
    expectations = [case.expectations["escalate"] for case in ESCALATION_SUITE.cases]
    assert True in expectations and False in expectations
    assert reports["escalation"].failed == 0


# --------------------------------------------------------------------------
# Prompts


def test_every_prompt_is_registered() -> None:
    assert len(ALL_PROMPTS) == len(PINNED_PROMPT_DIGESTS)
    assert {version for version, _ in ALL_PROMPTS} == set(PINNED_PROMPT_DIGESTS)


def test_prompt_text_matches_its_pinned_version() -> None:
    """A version that no longer identifies its text makes every log line lie."""
    drifted = [
        version
        for version, text in ALL_PROMPTS
        if digest(text) != PINNED_PROMPT_DIGESTS[version]
    ]
    assert drifted == [], (
        f"Prompt text changed without a version bump: {drifted}. "
        "Bump the version in app/llm/prompts.py, mirror the change into "
        "docs/prompt.md, and repin the digest here in the same commit."
    )


def test_prompt_versions_are_unique() -> None:
    versions = [version for version, _ in ALL_PROMPTS]
    assert len(versions) == len(set(versions))


def test_every_prompt_carries_a_version_and_text() -> None:
    for version, text in ALL_PROMPTS:
        assert version and "/" in version, version
        assert text.strip()


def test_the_digest_is_sensitive_to_a_single_character() -> None:
    """Proves the pin would actually notice an edit."""
    _, text = ALL_PROMPTS[0]
    assert digest(text) != digest(text + " ")
