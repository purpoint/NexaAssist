"""The container's build-time promises, checked without building it.

These read the Dockerfile and the lock file as text. That is deliberate: a
Docker daemon is not available everywhere the suite runs, and the properties
that matter most here -- no secret reaches the image, nothing migrates on boot,
the process is not root -- are decidable from the file itself. Whether the
image actually builds is a separate check, run where a daemon exists.
"""

import re
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
    """Dockerfile lines with comments and blanks removed."""
    return [
        line.strip()
        for line in dockerfile.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


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
