"""What the local stack promises, read from compose.yaml.

Parsed rather than started: bringing four containers up is not something a
unit suite should do on every run, and the properties that would hurt most if
they regressed -- a password in the file, a database published over the one a
developer already runs, a migration that applies itself -- are all decidable
from the definition. Whether the stack actually comes up is checked by hand
where a daemon exists.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"
FRONTEND_DOCKERFILE = ROOT / "frontend" / "Dockerfile"

# Reachable only from inside the compose network. Publishing either would put
# a second server beside the one a developer already runs locally.
PRIVATE_SERVICES = ("db", "redis")


@pytest.fixture(scope="module")
def raw() -> str:
    return COMPOSE.read_text()


@pytest.fixture(scope="module")
def stack(raw: str) -> dict:
    return yaml.safe_load(raw)


@pytest.fixture(scope="module")
def services(stack: dict) -> dict:
    return stack["services"]


# --------------------------------------------------------------------------
# No credential is written down


def test_the_database_password_has_no_default(raw: str) -> None:
    """`:?` is compose's required-variable form: absent, it refuses to start.

    A default would be a password in the git history that some deployment
    eventually keeps.
    """
    assert "${NEXA_DB_PASSWORD:?" in raw
    assert "${NEXA_DB_PASSWORD:-" not in raw, "a default password is still a password"


def test_no_secret_is_written_into_the_file(raw: str, services: dict) -> None:
    for name, service in services.items():
        for key, value in (service.get("environment") or {}).items():
            if not any(
                token in key
                for token in ("PASSWORD", "KEY", "SECRET", "TOKEN", "DATABASE_URL")
            ):
                continue
            assert "${" in str(value), f"{name}.{key} is a literal credential"


def test_the_provider_key_is_injected_never_written(services: dict) -> None:
    """It reaches the container from .env at runtime, and no further.

    Runtime injection is the whole point: the image itself must not contain
    it, which the container guards assert separately.
    """
    backend = services["backend"]
    assert "GROQ_API_KEY" not in (backend.get("environment") or {})
    env_files = backend.get("env_file") or []
    assert any(entry.get("path") == ".env" for entry in env_files)
    # Optional, so the stack starts on a machine that has never had one.
    assert all(entry.get("required") is False for entry in env_files)


# --------------------------------------------------------------------------
# Nothing migrates on its own


def test_migrations_are_behind_a_profile(services: dict) -> None:
    """`docker compose up` must not be able to change a schema."""
    assert services["migrate"]["profiles"], "migrate would start with `up`"


def test_nothing_waits_on_the_migration(services: dict) -> None:
    """A dependency on it would start it, profile or not."""
    for name, service in services.items():
        assert "migrate" not in (service.get("depends_on") or {}), name


def test_only_the_migration_service_runs_alembic(services: dict) -> None:
    for name, service in services.items():
        if name == "migrate":
            assert service["command"] == ["alembic", "upgrade", "head"]
            continue
        assert "alembic" not in str(service.get("command", "")), name


# --------------------------------------------------------------------------
# The stack does not reach outside itself


@pytest.mark.parametrize("name", PRIVATE_SERVICES)
def test_the_data_stores_publish_no_ports(services: dict, name: str) -> None:
    """The failure mode of a stray published 5432 is writing to the wrong
    database, which is not a failure anyone notices immediately."""
    assert not services[name].get("ports"), name


@pytest.mark.parametrize("name", ("backend", "frontend"))
def test_published_ports_are_bound_to_the_loopback(services: dict, name: str) -> None:
    """Without an address Docker binds every interface, which puts a
    development stack on whatever network the laptop is joined to."""
    for mapping in services[name]["ports"]:
        assert str(mapping).startswith("127.0.0.1:"), mapping


def test_redis_is_this_stacks_own(services: dict) -> None:
    """Addressed by service name, so it cannot resolve to a Redis outside.

    A URL pointing at the host would mix NexaAssist keys into whichever Redis
    the developer happens to be running.
    """
    url = services["backend"]["environment"]["REDIS_URL"]
    assert url.startswith("redis://redis:6379/")
    for outside in ("host.docker.internal", "localhost", "127.0.0.1"):
        assert outside not in url


def test_redis_keys_are_namespaced_to_this_project(services: dict) -> None:
    namespace = services["backend"]["environment"]["REDIS_NAMESPACE"]
    assert namespace.startswith("nexaassist:")


def test_the_database_is_reached_by_service_name(services: dict) -> None:
    for name in ("backend", "migrate"):
        url = services[name]["environment"]["DATABASE_URL"]
        assert "@db:5432/" in url, name
        assert url.startswith("postgresql+asyncpg://"), name


# --------------------------------------------------------------------------
# It comes up in an order that works


def test_every_long_running_service_reports_its_own_health(services: dict) -> None:
    """`depends_on` without a condition only waits for a container to exist,
    which for Postgres is several seconds before it accepts connections.

    One-shot tasks are exempt, and only those: a migration is healthy by
    exiting zero, and a healthcheck on it would be asking a finished process
    how it feels.
    """
    for name, service in services.items():
        if service.get("profiles"):
            continue
        assert service.get("healthcheck"), name


def test_the_backend_waits_for_its_dependencies(services: dict) -> None:
    depends = services["backend"]["depends_on"]
    for name in PRIVATE_SERVICES:
        assert depends[name]["condition"] == "service_healthy", name


def test_the_database_healthcheck_names_the_application_user(services: dict) -> None:
    """Bare pg_isready asks about the root user and answers about a server the
    application may still be unable to log into."""
    assert "-U" in " ".join(services["db"]["healthcheck"]["test"])


def test_postgres_can_create_the_vector_extension(services: dict) -> None:
    """The first migration runs CREATE EXTENSION vector; stock postgres cannot."""
    assert "pgvector" in services["db"]["image"]


def test_the_database_survives_a_restart(stack: dict, services: dict) -> None:
    """An anonymous volume is discarded on the next `up`, taking the local
    database with it."""
    assert any(v.startswith("pgdata:") for v in services["db"]["volumes"])
    assert "pgdata" in stack["volumes"]


# --------------------------------------------------------------------------
# The client image


def test_the_client_build_takes_no_credential() -> None:
    """A build argument is recorded in the image's history, so a secret passed
    as one is readable by anybody holding the image."""
    dockerfile = FRONTEND_DOCKERFILE.read_text()
    for forbidden in ("GROQ_API_KEY", "AUTH_API_KEYS", "DATABASE_URL", "PASSWORD"):
        assert forbidden not in dockerfile, forbidden


def test_the_client_never_copies_the_repository_wholesale() -> None:
    lines = [
        line.strip()
        for line in FRONTEND_DOCKERFILE.read_text().splitlines()
        if line.strip().upper().startswith("COPY ")
    ]
    assert lines
    for line in lines:
        assert ".env" not in line, line
        assert not line.split()[-2] == ".", line


def test_the_client_serves_as_a_non_root_user() -> None:
    """nginx's stock image starts as root to bind port 80; the unprivileged
    build listens high and never needs to."""
    assert "nginx-unprivileged" in FRONTEND_DOCKERFILE.read_text()


def test_node_does_not_ship_in_the_client_image() -> None:
    """The build needs a toolchain; serving static files does not."""
    stages = [
        line
        for line in FRONTEND_DOCKERFILE.read_text().splitlines()
        if line.upper().startswith("FROM ")
    ]
    assert len(stages) >= 2
    assert "node" not in stages[-1]
