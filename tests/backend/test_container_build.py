"""The container's build-time promises, checked without building it.

These read the Dockerfile and the lock file as text. That is deliberate: a
Docker daemon is not available everywhere the suite runs, and the properties
that matter most here -- no secret reaches the image, nothing migrates on boot,
the process is not root -- are decidable from the file itself. Whether the
image actually builds is a separate check, run where a daemon exists.
"""

import re
from fnmatch import fnmatch
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
DOCKERFILE = BACKEND / "Dockerfile"
LOCKFILE = BACKEND / "requirements.lock"
REQUIREMENTS = BACKEND / "requirements.txt"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text()


@pytest.fixture(scope="module")
def instructions(dockerfile: str) -> list[str]:
    """One entry per Dockerfile instruction, comments and blanks removed.

    Continuations are joined, so a multi-line ENV is one instruction and --
    the case that matters -- a HEALTHCHECK's own CMD is part of the
    HEALTHCHECK rather than a second command the container would run.
    """
    joined: list[str] = []
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if joined and joined[-1].endswith("\\"):
            joined[-1] = f"{joined[-1][:-1].strip()} {stripped}"
        else:
            joined.append(stripped)
    return [line.removesuffix("\\").strip() for line in joined]


def directives(instructions: list[str], keyword: str) -> list[str]:
    return [line for line in instructions if line.upper().startswith(f"{keyword} ")]


# --------------------------------------------------------------------------
# Nothing secret enters the image


def test_the_build_never_copies_the_repository_wholesale(
    instructions: list[str],
) -> None:
    """`COPY . .` is how a git-ignored .env reaches a pushed image.

    The repository root holds one with a real provider key. Every COPY names
    its source, so the wildcard case cannot arise by accident.
    """
    for line in directives(instructions, "COPY"):
        assert not re.search(r"COPY\s+(--\S+\s+)*\.\s", line), line
        assert " ./ " not in line, line


def test_no_environment_file_is_copied(instructions: list[str]) -> None:
    # Instructions, not the whole file: the comments explain why .env is
    # absent, and a comment saying so is not a copy of it.
    assert all(".env" not in line for line in instructions)


def test_no_credential_is_baked_into_the_image(dockerfile: str) -> None:
    """A key in an ENV or ARG survives in the image's layer metadata forever."""
    for forbidden in ("GROQ_API_KEY", "AUTH_API_KEYS", "DATABASE_URL", "REDIS_URL"):
        assert forbidden not in dockerfile, forbidden
    assert not re.search(r"gsk_[A-Za-z0-9_-]{8,}", dockerfile)


def test_only_the_application_is_copied(instructions: list[str]) -> None:
    sources = [line.split()[-2] for line in directives(instructions, "COPY")]
    assert set(sources) <= {"app", "alembic", "alembic.ini", "requirements.lock", "/opt/venv"}


# --------------------------------------------------------------------------
# Nothing migrates on boot


def test_the_container_does_not_migrate_on_startup(dockerfile: str) -> None:
    """Applying a migration is an operator's decision about a chosen database.

    A container that migrates on boot makes that decision for them, and makes
    it once per replica.
    """
    lowered = dockerfile.lower()
    assert "alembic upgrade" not in lowered
    assert "entrypoint" not in lowered, "no entrypoint script to hide a migration in"


def test_the_command_only_serves(instructions: list[str]) -> None:
    command = directives(instructions, "CMD")
    assert len(command) == 1
    assert "uvicorn" in command[0]
    assert "alembic" not in command[0]


def test_the_application_never_creates_schema() -> None:
    """The M3 rule, restated where a container could be tempted to break it."""
    sources = (BACKEND / "app").rglob("*.py")  # noqa: E501
    offenders = [p.name for p in sources if "create_all(" in p.read_text()]
    assert offenders == []


# --------------------------------------------------------------------------
# Runtime posture


def test_the_process_does_not_run_as_root(instructions: list[str]) -> None:
    users = directives(instructions, "USER")
    assert users, "an image with no USER runs as root"
    assert users[-1].split()[1] != "root"


def test_the_runtime_user_cannot_log_in(dockerfile: str) -> None:
    assert "nologin" in dockerfile


def test_the_application_is_not_writable_by_the_runtime_user(
    instructions: list[str],
) -> None:
    """Owned by the user, but the point is it is a fixed, known owner.

    A container that can rewrite its own code is one an attacker can persist
    in; ownership is stated explicitly rather than inherited from root.
    """
    app_copies = [line for line in directives(instructions, "COPY") if " app " in line]
    assert app_copies and all("--chown=" in line for line in app_copies)


def test_reload_is_not_enabled(instructions: list[str]) -> None:
    """It watches the filesystem and restarts on change -- a dev convenience."""
    assert all("--reload" not in line for line in instructions)


def test_python_runs_unbuffered(dockerfile: str) -> None:
    """Buffered output means logs vanish when a container is killed."""
    assert "PYTHONUNBUFFERED=1" in dockerfile


# --------------------------------------------------------------------------
# Deterministic dependencies


def test_the_image_installs_from_the_lock_file(instructions: list[str]) -> None:
    """Ranges resolve differently over time; an image should not."""
    installs = [line for line in instructions if "pip install" in line]
    assert installs and all("requirements.lock" in line for line in installs)
    assert all("requirements.txt" not in line for line in instructions)


def test_every_direct_dependency_is_pinned() -> None:
    """The lock must actually cover what the project declares."""
    declared = {
        re.split(r"[<>=!\[]", line, maxsplit=1)[0].strip().lower()
        for line in REQUIREMENTS.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    pinned = {
        line.split("==")[0].strip().lower().replace("_", "-")
        for line in LOCKFILE.read_text().splitlines()
        if "==" in line and not line.startswith("#")
    }
    missing = {name.replace("_", "-") for name in declared} - pinned
    assert missing == set(), missing


def test_every_lock_entry_is_an_exact_version() -> None:
    loose = [
        line
        for line in LOCKFILE.read_text().splitlines()
        if line.strip() and not line.startswith("#") and "==" not in line
    ]
    assert loose == []


def test_the_lock_contains_no_development_dependencies() -> None:
    """A test runner in a production image is extra surface for no benefit."""
    pinned = LOCKFILE.read_text().lower()
    for dev_only in ("pytest", "pyflakes", "httpx2"):
        assert f"\n{dev_only}==" not in f"\n{pinned}", dev_only


# --------------------------------------------------------------------------
# The build context

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

CONTEXTS = {"backend": BACKEND, "frontend": FRONTEND}


def ignore_patterns(context: Path) -> list[str]:
    return [
        line.strip()
        for line in (context / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def excluded(patterns: list[str], path: str) -> bool:
    """Does any pattern exclude ``path``, accounting for negations?

    Docker applies patterns in order and a later `!pattern` re-includes, so
    the last one to match decides. Approximate, but exact enough for the
    filenames these tests care about.
    """
    verdict = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        candidate = pattern.removeprefix("!").rstrip("/")
        if fnmatch(path, candidate) or fnmatch(path, f"{candidate}/*"):
            verdict = not negated
    return verdict


@pytest.mark.parametrize("name", sorted(CONTEXTS))
def test_each_build_context_has_an_ignore_file(name: str) -> None:
    """The Dockerfiles copy by name, so this is the second defence.

    It is the one that matters on the day somebody writes `COPY . .`: two
    independent things must then go wrong before a key reaches an image.
    """
    assert (CONTEXTS[name] / ".dockerignore").is_file()


@pytest.mark.parametrize("name", sorted(CONTEXTS))
@pytest.mark.parametrize("secret", (".env", ".env.local", ".env.production"))
def test_no_environment_file_can_enter_the_context(name: str, secret: str) -> None:
    """Every variant, not just `.env`.

    A pattern that catches `.env` and misses `.env.local` is worse than none,
    because it reads like coverage.
    """
    assert excluded(ignore_patterns(CONTEXTS[name]), secret), secret


def test_the_backend_context_excludes_development_only_inputs() -> None:
    patterns = ignore_patterns(BACKEND)
    for path in ("tests", "requirements-dev.txt", "__pycache__", ".venv"):
        assert excluded(patterns, path), path


def test_the_client_context_excludes_host_built_output() -> None:
    """node_modules holds packages compiled for the developer's platform; the
    builder installs the same lock file for the image's."""
    patterns = ignore_patterns(FRONTEND)
    for path in ("node_modules", "dist"):
        assert excluded(patterns, path), path


def test_the_lock_file_still_reaches_the_backend_context() -> None:
    """The ignore file must not exclude what the build needs."""
    patterns = ignore_patterns(BACKEND)
    for path in ("requirements.lock", "app", "alembic", "alembic.ini"):
        assert not excluded(patterns, path), path


# --------------------------------------------------------------------------
# The image describes itself


def test_the_image_health_checks_itself(dockerfile: str) -> None:
    """In the image, not only in compose: anything that runs it gets the same
    answer to "is it up?"."""
    assert "HEALTHCHECK" in dockerfile
    assert "/api/v1/health" in dockerfile


def test_the_image_says_where_it_came_from(dockerfile: str) -> None:
    assert "org.opencontainers.image.source" in dockerfile
