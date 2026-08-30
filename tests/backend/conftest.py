"""Shared fixtures for the backend test suite."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """The application settings the app under test is built from."""
    return get_settings()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A client bound to a freshly built app, with lifespan events run."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client
