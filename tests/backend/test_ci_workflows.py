"""What continuous integration promises, read from the workflow files.

A workflow is configuration that runs with a token, on a machine nobody is
watching, against every push. The failure modes worth guarding are the quiet
ones: a job that reads a secret it does not need, a token with write access it
never uses, a `pip install` that silently drifts from the lock file.

YAML parses `on:` as the boolean True -- that is YAML 1.1 and not a typo here.
"""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
TESTS_WORKFLOW = WORKFLOWS / "tests.yml"

ON = True  # the parsed form of `on:`


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


def steps(job: dict) -> list[dict]:
    return job.get("steps") or []


@pytest.fixture(scope="module")
def tests_workflow() -> dict:
    return load(TESTS_WORKFLOW)


# --------------------------------------------------------------------------
# Nothing here holds a credential


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_no_workflow_reads_a_secret(path: Path) -> None:
    """None of these jobs needs one.

    The suite blocks outbound connections, so a test that reached a real
    provider would fail rather than bill someone -- which only holds while no
    workflow hands it a key to reach one with.
    """
    # The expression, not the English word: a comment explaining that a
    # workflow reads no secrets should not fail a test for saying so.
    assert not re.search(r"\$\{\{\s*secrets\.", path.read_text()), path.name


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_no_workflow_contains_a_literal_credential(path: Path) -> None:
    text = path.read_text()
    for marker in ("gsk_", "GROQ_API_KEY", "AUTH_API_KEYS", "-----BEGIN"):
        assert marker not in text, marker


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_the_token_is_read_only(path: Path) -> None:
    """Without this the token can write to the repository.

    Every job here reads code and runs tests; none of them pushes anything.
    """
    assert load(path)["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_the_checkout_leaves_no_token_behind(path: Path) -> None:
    """actions/checkout writes the token into .git/config by default, where
    anything the build runs can read it."""
    for job in load(path)["jobs"].values():
        for step in steps(job):
            if str(step.get("uses", "")).startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] is False


# --------------------------------------------------------------------------
# It runs on the changes that matter


def test_it_runs_on_pushes_and_pull_requests(tests_workflow: dict) -> None:
    triggers = tests_workflow[ON]
    assert "pull_request" in triggers
    assert "main" in triggers["push"]["branches"]


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_every_job_has_a_timeout(path: Path) -> None:
    """A job with no timeout waits for GitHub's six-hour default, which is
    how a hung test run turns into a wasted afternoon."""
    for name, job in load(path)["jobs"].items():
        assert job.get("timeout-minutes"), name


# --------------------------------------------------------------------------
# It runs what it claims to


def test_the_backend_job_lints_and_tests(tests_workflow: dict) -> None:
    commands = " ".join(
        str(step.get("run", "")) for step in steps(tests_workflow["jobs"]["backend"])
    )
    assert "pyflakes" in commands
    assert "pytest" in commands


def test_the_backend_job_installs_the_declared_dependencies(
    tests_workflow: dict,
) -> None:
    commands = " ".join(
        str(step.get("run", "")) for step in steps(tests_workflow["jobs"]["backend"])
    )
    assert "requirements-dev.txt" in commands


def test_the_backend_job_runs_the_version_the_image_runs(
    tests_workflow: dict,
) -> None:
    """Testing what ships is worth more than testing the newest interpreter."""
    setup = [
        step
        for step in steps(tests_workflow["jobs"]["backend"])
        if str(step.get("uses", "")).startswith("actions/setup-python")
    ]
    assert setup and setup[0]["with"]["python-version"] == "3.12"


def test_the_frontend_job_typechecks_and_tests(tests_workflow: dict) -> None:
    commands = [
        str(step.get("run", "")) for step in steps(tests_workflow["jobs"]["frontend"])
    ]
    assert "npm run typecheck" in commands
    assert "npm run test" in commands


def test_the_frontend_job_installs_exactly_the_lock_file(
    tests_workflow: dict,
) -> None:
    """`npm install` will happily resolve something the lock file does not
    name, which makes a green build a statement about nothing in particular."""
    commands = [
        str(step.get("run", "")) for step in steps(tests_workflow["jobs"]["frontend"])
    ]
    assert "npm ci" in commands
    assert "npm install" not in commands


def test_no_job_migrates_a_database(tests_workflow: dict) -> None:
    """Nothing in this workflow has a database to migrate, and a workflow that
    grew one should not migrate it as a side effect of running tests."""
    for name, job in tests_workflow["jobs"].items():
        commands = " ".join(str(step.get("run", "")) for step in steps(job))
        assert "alembic upgrade" not in commands, name


# --------------------------------------------------------------------------
# The workflow that needs real infrastructure

INTEGRATION_WORKFLOW = WORKFLOWS / "integration.yml"


@pytest.fixture(scope="module")
def integration_workflow() -> dict:
    return load(INTEGRATION_WORKFLOW)


def test_the_suite_runs_only_against_the_dedicated_test_database(
    integration_workflow: dict,
) -> None:
    """The suite's own guard refuses any other database.

    Pointing CI at one it would refuse turns a safety property into a failed
    job nobody can explain; pointing it at a real one would be worse.
    """
    for step in steps(integration_workflow["jobs"]["services"]):
        url = (step.get("env") or {}).get("TEST_DATABASE_URL")
        if url:
            assert url.endswith("/nexaassist_test"), url


def test_redis_is_confined_to_the_test_index(integration_workflow: dict) -> None:
    """A Redis server is shared far more often than a database is."""
    for step in steps(integration_workflow["jobs"]["services"]):
        url = (step.get("env") or {}).get("TEST_REDIS_URL")
        if url:
            assert url.endswith("/15"), url


def test_the_database_service_can_create_the_vector_extension(
    integration_workflow: dict,
) -> None:
    postgres = integration_workflow["jobs"]["services"]["services"]["postgres"]
    assert "pgvector" in postgres["image"]


def test_no_password_is_written_for_the_service_containers(
    integration_workflow: dict,
) -> None:
    """Trust authentication instead: the container is ephemeral, reachable
    only from its own job, and destroyed with it. A password in a public
    workflow file would be strictly worse and no more secure."""
    postgres = integration_workflow["jobs"]["services"]["services"]["postgres"]
    assert "POSTGRES_PASSWORD" not in postgres["env"]
    assert postgres["env"]["POSTGRES_HOST_AUTH_METHOD"] == "trust"


@pytest.mark.parametrize("service", ("postgres", "redis"))
def test_every_service_container_is_waited_for(
    integration_workflow: dict, service: str
) -> None:
    """Without a health option the job starts the moment the container
    exists, which for PostgreSQL is seconds before it accepts connections."""
    options = integration_workflow["jobs"]["services"]["services"][service]["options"]
    assert "--health-cmd" in options


def test_the_stack_password_is_generated_rather_than_written(
    integration_workflow: dict,
) -> None:
    """The stack requires the variable and defaults it to nothing, which is
    as much what this job exercises as the stack coming up."""
    commands = " ".join(
        str(step.get("run", "")) for step in steps(integration_workflow["jobs"]["stack"])
    )
    assert "openssl rand" in commands
    assert "NEXA_DB_PASSWORD=" in commands


def test_the_stack_migrates_only_after_it_is_up(integration_workflow: dict) -> None:
    """Ordering, asserted: a migration folded into `up` would be the exact
    thing the compose file is built to prevent."""
    commands = [
        str(step.get("run", "")) for step in steps(integration_workflow["jobs"]["stack"])
    ]
    started = next(i for i, c in enumerate(commands) if "compose up" in c)
    migrated = next(i for i, c in enumerate(commands) if "profile migrate run" in c)
    assert started < migrated
    assert "migrate" not in commands[started]


def test_the_stack_is_always_torn_down(integration_workflow: dict) -> None:
    teardown = [
        step
        for step in steps(integration_workflow["jobs"]["stack"])
        if "compose" in str(step.get("run", "")) and "down" in str(step.get("run", ""))
    ]
    assert teardown and all(step.get("if") == "always()" for step in teardown)
