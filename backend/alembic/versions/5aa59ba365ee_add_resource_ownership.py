"""add resource ownership

Adds ``owner_subject`` to the two tables with a read surface that could
otherwise return another subject's rows: conversations and tickets.

Nullable on purpose. Rows predating ownership have no owner, and so does every
row created by a deployment that does not scope by subject. Backfilling would
mean inventing an owner for data whose owner is genuinely unknown.

Indexed because every scoped read filters on it.

**Autogenerate proposed dropping three enum CHECK constraints here** --
``ck_conversation_messages_message_role_valid``,
``ck_review_items_review_status_valid`` and ``ck_tickets_status_valid`` -- none
of which this change touches. That is the known behaviour of
``Enum(create_constraint=True)`` under autogenerate, documented in
docs/architecture.md, and the drops were removed by hand. A guard test asserts
no migration carries them.

Revision ID: 5aa59ba365ee
Revises: 6cc81eefa78f
Create Date: 2026-09-03 21:55:07.497169

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5aa59ba365ee'
down_revision: Union[str, Sequence[str], None] = '6cc81eefa78f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the ownership column and its index to both tables."""
    op.add_column(
        'conversations', sa.Column('owner_subject', sa.String(length=200), nullable=True)
    )
    op.create_index(
        op.f('ix_conversations_owner_subject'),
        'conversations',
        ['owner_subject'],
        unique=False,
    )
    op.add_column(
        'tickets', sa.Column('owner_subject', sa.String(length=200), nullable=True)
    )
    op.create_index(
        op.f('ix_tickets_owner_subject'), 'tickets', ['owner_subject'], unique=False
    )


def downgrade() -> None:
    """Drop the ownership column and its index from both tables."""
    op.drop_index(op.f('ix_tickets_owner_subject'), table_name='tickets')
    op.drop_column('tickets', 'owner_subject')
    op.drop_index(op.f('ix_conversations_owner_subject'), table_name='conversations')
    op.drop_column('conversations', 'owner_subject')
