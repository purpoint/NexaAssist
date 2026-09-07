"""add citations to conversation messages

Adds the sources behind an assistant turn to the turn itself, so a reopened
conversation shows what its answers were based on. Before this, provenance
existed only in the live response and was lost the moment the page was
reloaded -- the same question answered twice, once with citations and once
without, depending on whether you were still looking at it.

JSONB holding a snapshot rather than foreign keys into document chunks: a
citation describes what an answer was based on when it was given, and
resolving a reference after the document changed would show text the customer
never saw. See the column's comment in app/models/conversation.py.

Backfilled as an empty array, which is the honest value for rows written
before the column existed: nothing was recorded, so nothing is claimed.

Autogenerate also proposed dropping three enum CHECK constraints -- on
conversation_messages.role, review_items.status and tickets.status. Those
drops are spurious and have been removed. They come from the project's known
hazard with ``native_enum=False`` and ``create_constraint=True``: Alembic
compares the rendered constraint against the reflected one, does not match
them, and proposes a drop every time. Applying it would remove the constraint
that keeps an invalid role or status out of the table.

Revision ID: 6229d20c7056
Revises: 5aa59ba365ee
Create Date: 2026-09-07 22:54:59.399978

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '6229d20c7056'
down_revision: Union[str, Sequence[str], None] = '5aa59ba365ee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the column, defaulting existing rows to no recorded sources."""
    op.add_column(
        'conversation_messages',
        sa.Column(
            'citations',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop the column.

    Lossy, and unavoidably so: the citations recorded since the upgrade have
    nowhere else to live. Everything else about a conversation survives.
    """
    op.drop_column('conversation_messages', 'citations')
