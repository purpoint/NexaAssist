"""Fixtures for the tests that build and run real images.

Three jobs, scoped to this directory only:

* Narrow the suite-wide ``no_network`` guard rather than removing it. These
  tests talk to a container on this machine; replacing the guard with
  "anything goes" is what once let a test reach a real provider with a real
  key, billed and unnoticed. So: localhost only.
* Skip everything when no Docker daemon is reachable, so the suite still
  passes on a machine or CI runner without one.
* Build each image once per session and tear down every container started.

Everything here is tagged ``nexaassist-*:test``. Nothing touches an image or
container it did not create, and no test removes anything by pattern.
"""

import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

BACKEND_IMAGE = "nexaassist-backend:test"
FRONTEND_IMAGE = "nexaassist-frontend:test"

# Well clear of the ports a developer is likely to be using, and of the ones
# compose publishes.
BACKEND_PORT = 18099


def docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Localhost only -- the narrowed form, not a removal."""

    def check(address: object) -> None:
        if isinstance(address, tuple) and address:
            host = address[0]
            if host not in {"localhost", "127.0.0.1", "::1", ""}:
                raise RuntimeError(
                    f"Only local connections are allowed in the test suite; got {host!r}."
                )

    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def guarded_connect(self: socket.socket, address: object) -> object:
        check(address)
        return real_connect(self, address)

    def guarded_create(address: object, *args: object, **kwargs: object) -> object:
        check(address)
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create)


@pytest.fixture(autouse=True)
def require_docker() -> None:
    if not docker_available():
        pytest.skip("Docker daemon not reachable")


def docker(*args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    """Run a docker command and insist it succeeded."""
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    assert result.returncode == 0, f"docker {' '.join(args)}\n{result.stderr}"
    return result


def build(context: str, tag: str) -> str:
    docker(
        "build",
        "-f",
        f"{context}/Dockerfile",
        "-t",
        tag,
        context,
    )
    return tag


@pytest.fixture(scope="session")
def backend_image(request: pytest.FixtureRequest) -> str:
    if not docker_available():
        pytest.skip("Docker daemon not reachable")
    return build("backend", BACKEND_IMAGE)


@pytest.fixture(scope="session")
def frontend_image(request: pytest.FixtureRequest) -> str:
    if not docker_available():
        pytest.skip("Docker daemon not reachable")
    return build("frontend", FRONTEND_IMAGE)


@pytest.fixture(scope="session")
def running_backend(backend_image: str) -> Iterator[str]:
    """The backend image, actually serving, on the loopback.

    No database and no provider key: the container is started with an empty
    environment on purpose, so what this proves is that the image runs and
    answers -- not that a particular developer's .env works.
    """
    container = docker(
        "run",
        "--rm",
        "--detach",
        "--publish",
        f"127.0.0.1:{BACKEND_PORT}:8000",
        backend_image,
    ).stdout.strip()
    try:
        _wait_for_health(BACKEND_PORT)
        yield f"http://127.0.0.1:{BACKEND_PORT}"
    finally:
        # By id, never by name pattern: this removes the container this
        # fixture started and no other.
        subprocess.run(
            ["docker", "rm", "--force", container], capture_output=True, timeout=60
        )


def _wait_for_health(port: int, attempts: int = 60) -> None:
    """Wait for a real answer, not for a socket.

    Docker publishes the port by proxying it, and the proxy accepts
    connections from the moment the container exists -- several seconds
    before uvicorn is listening behind it. Waiting for a TCP connect
    therefore succeeds immediately and hands the first request a closed
    connection. An HTTP 200 is the only signal that means what it looks like.
    """
    url = f"http://127.0.0.1:{port}/api/v1/health"
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as reply:
                if reply.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            # A refused connection comes back instantly, so the attempt is not
            # itself the delay -- without this sleep the loop spends its sixty
            # tries in under a second and gives up before the server exists.
            time.sleep(0.5)
    raise AssertionError(f"{url} never answered after {attempts} attempts")
