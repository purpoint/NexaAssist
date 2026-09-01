"""Migration configuration and chain integrity.

Offline: nothing here connects to a database. The tests that actually run
migrations live in ``tests/backend/db/test_migrations.py``.
"""

import configparser
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2] / "backend"
ALEMBIC_INI = BACKEND / "alembic.ini"
VERSIONS = BACKEND / "alembic" / "versions"


def script_directory() -> ScriptDirectory:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(config)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_alembic_ini_exists() -> None:
    assert ALEMBIC_INI.is_file()


def test_ini_declares_no_database_url() -> None:
    """A URL in a tracked file is a credential waiting to be committed.

    The URL comes from Settings via env.py instead -- one source of truth.
    """
    parser = configparser.ConfigParser()
    parser.read(ALEMBIC_INI)

    assert not parser.has_option("alembic", "sqlalchemy.url")


def test_ini_contains_no_credentials() -> None:
    """No connection string, commented out or otherwise."""
    import re

    for line in ALEMBIC_INI.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not re.search(r"://[^\s]*:[^\s]*@", stripped), stripped
        assert not stripped.lower().startswith("sqlalchemy.url"), stripped


def test_env_resolves_the_url_from_application_settings() -> None:
    """env.py must read Settings, not a duplicated config block."""
    source = (BACKEND / "alembic" / "env.py").read_text()

    assert "from app.core.config import get_settings" in source
    assert "settings.database_url" in source


def test_env_imports_model_metadata_for_autogenerate() -> None:
    source = (BACKEND / "alembic" / "env.py").read_text()

    assert "from app.models import metadata as target_metadata" in source


# --------------------------------------------------------------------------
# Chain integrity
# --------------------------------------------------------------------------


def test_there_is_exactly_one_head() -> None:
    """A branched history is far cheaper to catch here than after a merge."""
    assert len(script_directory().get_heads()) == 1


def test_the_chain_has_a_single_root() -> None:
    roots = [r for r in script_directory().walk_revisions() if r.down_revision is None]

    assert len(roots) == 1


def test_every_revision_defines_upgrade_and_downgrade() -> None:
    """No migration may be one-way; rollback has to stay possible."""
    for path in VERSIONS.glob("*.py"):
        source = path.read_text()
        assert "def upgrade()" in source, path.name
        assert "def downgrade()" in source, path.name


def test_at_least_the_baseline_revision_exists() -> None:
    revisions = list(script_directory().walk_revisions())

    assert len(revisions) >= 1
    assert any("baseline" in (r.doc or "") for r in revisions)


def test_migrations_declare_no_business_tables_yet() -> None:
    """M3 ships the foundation; domain tables belong to M4."""
    for path in VERSIONS.glob("*.py"):
        assert "create_table" not in path.read_text(), path.name


def test_no_migration_references_another_projects_database() -> None:
    for path in VERSIONS.glob("*.py"):
        text = path.read_text().lower()
        for other in ("deeptrace", "relay", "tracker", "wasteiq"):
            assert other not in text, f"{path.name} references {other}"


# --------------------------------------------------------------------------
# Failure mode when unconfigured
# --------------------------------------------------------------------------


def test_missing_database_url_fails_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raises before opening a connection, so this needs no database."""
    from alembic import command

    from app.core.config import get_settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND / "alembic"))

    try:
        import app.core.config as config_module

        monkeypatch.setattr(
            config_module, "get_settings", lambda: config_module.Settings(database_url=None)
        )
        with pytest.raises(RuntimeError, match="DATABASE_URL is not configured"):
            command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()
