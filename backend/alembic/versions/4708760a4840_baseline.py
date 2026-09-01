"""baseline

Establishes the migration chain and the ``alembic_version`` table.

Intentionally empty. M3 delivers the persistence foundation -- engine, session
lifecycle, declarative base, constraint naming -- and no business tables;
tickets, conversations, and customers belong to M4. Creating a table here to
make the baseline look substantial would take scope from that milestone.

An empty baseline is still doing work: it fixes the head every later revision
descends from, and it gives ``downgrade base`` somewhere to land.

Revision ID: 4708760a4840
Revises:
Create Date: 2026-09-01
"""

from collections.abc import Sequence

revision: str = "4708760a4840"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema changes; the baseline only anchors the chain."""


def downgrade() -> None:
    """Symmetrically empty, so ``downgrade base`` succeeds."""
