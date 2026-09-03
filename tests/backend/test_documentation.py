"""Documentation, checked against the thing it describes.

Prose rots quietly. A README that lists an endpoint which no longer exists is
worse than one that lists nothing, because a reader has no way to tell which
half is current -- so the claims here that can be checked mechanically are.

What is deliberately not checked is whether the prose is any good. These
assert that its factual claims are true, not that they are well made.
"""

import re
from pathlib import Path

import pytest

from app.core.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
OVERVIEW = ROOT / "docs" / "overview.md"
ENV_EXAMPLE = ROOT / ".env.example"

# Settings fields with a deliberate reason not to appear under their own name.
DOCUMENTED_ELSEWHERE = {
    # Read from GROQ_API_KEY, which is what the SDK itself looks for; the
    # example documents that name instead.
    "LLM_API_KEY",
}


@pytest.fixture(scope="module")
def example_variables() -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(), re.M))


@pytest.fixture(scope="module")
def schema() -> dict:
    return create_app().openapi()


# --------------------------------------------------------------------------
# Configuration is documented where the project says it is


def test_every_setting_appears_in_the_example(example_variables: set[str]) -> None:
    """The repository's own stated convention, enforced.

    A setting nobody documented is a setting nobody knows to set, which
    surfaces as behaviour somebody cannot explain.
    """
    declared = {name.upper() for name in Settings.model_fields}
    missing = declared - example_variables - DOCUMENTED_ELSEWHERE
    assert missing == set(), missing


def test_the_example_documents_nothing_that_does_not_exist(
    example_variables: set[str],
) -> None:
    """The other direction: a variable removed from the code and left in the
    example is a setting people will keep setting to no effect."""
    known = {name.upper() for name in Settings.model_fields}
    known |= {"GROQ_API_KEY"}  # the alias LLM_API_KEY is read from
    known |= {"VITE_API_BASE_URL"}  # the client's, inlined by Vite at build
    assert example_variables - known == set()


def test_the_example_carries_no_values_for_the_secrets(
    example_variables: set[str],
) -> None:
    """It is committed. The variables it must name and must never fill in."""
    text = ENV_EXAMPLE.read_text()
    for secret in ("GROQ_API_KEY", "AUTH_API_KEYS"):
        assert re.search(rf"^{secret}=\s*$", text, re.M), secret


# --------------------------------------------------------------------------
# The README describes the API that exists


def documented_endpoints() -> list[tuple[str, str]]:
    """(method, path) pairs from the README's endpoint table."""
    return [
        (method, path)
        for method, path in re.findall(r"`(GET|POST) (/[a-z0-9{}/_-]+)`", README.read_text())
    ]


def test_the_readme_documents_some_endpoints() -> None:
    """Otherwise the check below passes by describing nothing."""
    assert len(documented_endpoints()) >= 8


def test_every_documented_endpoint_exists(schema: dict) -> None:
    for method, path in documented_endpoints():
        full = f"/api/v1{path}"
        assert full in schema["paths"], full
        assert method.lower() in schema["paths"][full], f"{method} {full}"


def test_the_documented_prefix_is_the_served_one(schema: dict) -> None:
    assert "/api/v1" in README.read_text()
    assert all(path.startswith("/api/v1") for path in schema["paths"])


# --------------------------------------------------------------------------
# Nothing is still a placeholder


@pytest.mark.parametrize("document", (README, OVERVIEW), ids=lambda p: p.name)
def test_no_placeholder_survives(document: Path) -> None:
    """These two are the front door. A TBD in either is a promise to a reader
    that something exists and an admission that it does not."""
    text = document.read_text()
    for placeholder in ("TBD", "Placeholder", "to be filled in", "Coming soon"):
        assert placeholder not in text, placeholder


def test_the_readme_does_not_still_call_this_a_skeleton() -> None:
    """It said "foundation only -- no agents, retrieval, database, cache,
    authentication, or business workflows" until every one of those existed."""
    text = README.read_text().lower()
    for stale in ("foundation only", "not implemented yet", "intentionally **not"):
        assert stale not in text, stale


def test_the_overview_marks_capabilities_it_actually_has() -> None:
    """An unchecked box next to something that shipped is a lie by omission;
    a checked one next to something that did not is the worse direction."""
    text = OVERVIEW.read_text()
    assert "- [ ]" not in text, "an unchecked capability remains in the overview"
    assert text.count("- [x]") >= 8


# --------------------------------------------------------------------------
# The architecture and API documents describe what exists

ARCHITECTURE = ROOT / "docs" / "architecture.md"
API_REFERENCE = ROOT / "docs" / "api.md"


def test_the_architecture_diagram_lists_the_served_routes(schema: dict) -> None:
    """The diagram is the first thing anybody reads.

    A route in it that the service does not serve sends a reader looking for
    something that is not there; one it serves but the diagram omits is a
    reader who never learns it exists.
    """
    diagram = ARCHITECTURE.read_text()
    for path in schema["paths"]:
        assert path in diagram, path


def test_the_api_reference_documents_every_endpoint(schema: dict) -> None:
    reference = API_REFERENCE.read_text()
    for path in schema["paths"]:
        # Documented under the version prefix the whole file establishes once.
        assert path.removeprefix("/api/v1") in reference, path


def test_the_api_reference_describes_the_one_error_shape() -> None:
    """A client that can parse one error body can parse all of them, over
    both transports -- which is only useful if it is written down."""
    reference = API_REFERENCE.read_text()
    for field in ("code", "message", "details"):
        assert f'"{field}"' in reference or f"`{field}`" in reference, field


def test_the_deployment_question_is_answered() -> None:
    """It was open, and M22 is what closed it."""
    text = ARCHITECTURE.read_text()
    question = text.split("- Deployment target?")[1].split("\n- ")[0]
    assert "Still open" not in question
    assert "M22" in question


@pytest.mark.parametrize("milestone", ("M22", "M23"))
def test_the_shipped_milestones_have_a_section(milestone: str) -> None:
    """The document's own stated rule: each section names the milestone that
    introduced it, in milestone order."""
    assert f"({milestone})" in ARCHITECTURE.read_text(), milestone
