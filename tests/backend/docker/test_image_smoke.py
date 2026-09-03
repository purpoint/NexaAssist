"""What the built images actually are, checked by building and running them.

The Dockerfile guards elsewhere read a file and reason about what it says.
These build it and look. The difference matters for exactly the properties
worth being sure about: a base image can carry a secret the Dockerfile never
mentions, a build argument can end up in layer history, and "it starts" is not
something a text file can tell you.
"""

import json
import urllib.request

import pytest

from tests.backend.docker.conftest import docker

# Anything matching these in an image's filesystem or history is a finding.
CREDENTIAL_MARKERS = ("GROQ_API_KEY", "AUTH_API_KEYS", "gsk_")


def inspect(image: str) -> dict:
    return json.loads(docker("image", "inspect", image).stdout)[0]


def run(image: str, *command: str) -> str:
    return docker("run", "--rm", "--entrypoint", "sh", image, "-c", *command).stdout


# --------------------------------------------------------------------------
# No secret is in the image


def test_the_backend_image_contains_no_environment_file(backend_image: str) -> None:
    """The one that would have the provider key in it.

    /proc is excluded because it is not the image -- it is the running
    container's own kernel state, and find will happily descend into it.
    """
    found = run(
        backend_image,
        'find / -name ".env*" -not -path "/proc/*" -not -path "/sys/*" 2>/dev/null; true',
    )
    assert found.strip() == "", found


def test_the_frontend_image_contains_no_environment_file(frontend_image: str) -> None:
    """Worse here than in the backend: Vite inlines VITE_ variables into the
    bundle, so a .env in this context is a value served to every browser."""
    found = run(
        frontend_image,
        'find / -name ".env*" -not -path "/proc/*" -not -path "/sys/*" 2>/dev/null; true',
    )
    assert found.strip() == "", found


@pytest.mark.parametrize("image_fixture", ("backend_image", "frontend_image"))
def test_no_credential_is_in_the_image_configuration(
    request: pytest.FixtureRequest, image_fixture: str
) -> None:
    image = request.getfixturevalue(image_fixture)
    config = inspect(image)["Config"]
    environment = " ".join(config.get("Env") or [])
    for marker in CREDENTIAL_MARKERS:
        assert marker not in environment, marker


@pytest.mark.parametrize("image_fixture", ("backend_image", "frontend_image"))
def test_no_credential_survives_in_the_layer_history(
    request: pytest.FixtureRequest, image_fixture: str
) -> None:
    """A secret passed as a build argument is not in the final filesystem and
    is still readable: `docker history` keeps the instruction that used it."""
    image = request.getfixturevalue(image_fixture)
    history = docker("history", "--no-trunc", "--format", "{{.CreatedBy}}", image).stdout
    for marker in CREDENTIAL_MARKERS:
        assert marker not in history, marker


def test_the_client_bundle_carries_no_credential(frontend_image: str) -> None:
    """What is served is what a browser can read. Checked against the built
    assets rather than the source, because inlining happens at build time."""
    served = run(frontend_image, "cat /usr/share/nginx/html/assets/*.js")
    for marker in CREDENTIAL_MARKERS:
        assert marker not in served, marker


# --------------------------------------------------------------------------
# The runtime posture is what the Dockerfile claims


def test_the_backend_runs_as_the_unprivileged_account(backend_image: str) -> None:
    assert run(backend_image, "id -u").strip() == "10001"


def test_the_frontend_runs_unprivileged(frontend_image: str) -> None:
    assert run(frontend_image, "id -u").strip() != "0"


def test_the_backend_carries_no_compiler(backend_image: str) -> None:
    """The builder needed one to compile wheels. Shipping it would hand an
    attacker a toolchain in the same container as the application."""
    found = run(backend_image, "command -v gcc cc make || true")
    assert found.strip() == "", found


def test_the_backend_ships_no_test_runner(backend_image: str) -> None:
    found = run(backend_image, "command -v pytest || true")
    assert found.strip() == "", found


def test_no_build_cache_ships_in_the_backend_image(backend_image: str) -> None:
    """pip's cache is megabytes of wheels the running process never reads."""
    found = run(backend_image, 'ls /root/.cache 2>/dev/null; ls "$HOME/.cache" 2>/dev/null; true')
    assert found.strip() == "", found


def test_node_does_not_ship_in_the_client_image(frontend_image: str) -> None:
    found = run(frontend_image, "command -v node npm || true")
    assert found.strip() == "", found


@pytest.mark.parametrize("image_fixture", ("backend_image", "frontend_image"))
def test_the_image_reports_its_own_health(
    request: pytest.FixtureRequest, image_fixture: str
) -> None:
    """Declared on the image, so anything that runs it -- compose, a
    scheduler, a bare `docker run` -- gets the same answer to "is it up?"."""
    image = request.getfixturevalue(image_fixture)
    assert inspect(image)["Config"]["Healthcheck"]["Test"]


@pytest.mark.parametrize("image_fixture", ("backend_image", "frontend_image"))
def test_the_image_says_where_it_came_from(
    request: pytest.FixtureRequest, image_fixture: str
) -> None:
    image = request.getfixturevalue(image_fixture)
    labels = inspect(image)["Config"]["Labels"] or {}
    assert "NexaAssist" in labels.get("org.opencontainers.image.title", "")
    assert labels.get("org.opencontainers.image.source", "").startswith("https://")


# --------------------------------------------------------------------------
# It serves


def test_the_container_answers_when_started_with_nothing_configured(
    running_backend: str,
) -> None:
    """No database, no key, no environment at all.

    A container that needs a complete configuration before it will admit to
    being alive cannot be health-checked by anything that starts it.
    """
    with urllib.request.urlopen(f"{running_backend}/api/v1/health", timeout=10) as reply:
        assert reply.status == 200
        assert json.loads(reply.read())["status"] == "ok"


def test_readiness_reports_what_is_missing_rather_than_failing(
    running_backend: str,
) -> None:
    with urllib.request.urlopen(f"{running_backend}/api/v1/ready", timeout=10) as reply:
        body = json.loads(reply.read())
    assert body["components"]["database"] == "not_configured"


def test_the_container_does_not_migrate_what_it_is_pointed_at(
    running_backend: str,
) -> None:
    """It started with no DATABASE_URL and stayed up.

    An image that migrated on boot would either have failed here or -- far
    worse, given a URL -- have quietly changed a schema nobody asked it to.
    """
    logs = docker("ps", "--filter", "publish=18099", "--format", "{{.Status}}").stdout
    assert "Up" in logs
