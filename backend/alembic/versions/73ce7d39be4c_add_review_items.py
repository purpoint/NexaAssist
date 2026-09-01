"""add review items

The human review queue: requests the escalation criteria handed to a person.

Autogenerate again proposed dropping ``ck_tickets_status_valid`` -- see the
M5 revision for why it does that every time -- and it has been removed here.
A test asserts no migration ever carries that drop.

Revision ID: 73ce7d39be4c
Revises: 5f02b1107b3f
Create Date: 2026-09-01 23:22:20.246484

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "73ce7d39be4c"
down_revision: str | Sequence[str] | None = "5f02b1107b3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the review_items table."""
    op.create_table('review_items',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('ticket_id', sa.Uuid(), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('proposed_reply', sa.Text(), nullable=True),
    sa.Column('intent', sa.String(length=40), nullable=False),
    sa.Column('reason', sa.String(length=40), nullable=False),
    sa.Column('status', sa.Enum('pending', 'claimed', 'resolved', name='review_status_valid', native_enum=False, create_constraint=True, length=20), server_default='pending', nullable=False),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(btrim(message)) > 0', name=op.f('ck_review_items_message_not_blank')),
    sa.CheckConstraint('length(btrim(reason)) > 0', name=op.f('ck_review_items_reason_not_blank')),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], name=op.f('fk_review_items_ticket_id_tickets'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review_items'))
    )
    op.create_index('ix_review_items_status_created_at', 'review_items', ['status', 'created_at'], unique=False)
    op.create_index('ix_review_items_ticket_id', 'review_items', ['ticket_id'], unique=False)


def downgrade() -> None:
    """Drop the review_items table."""
    op.drop_index('ix_review_items_ticket_id', table_name='review_items')
    op.drop_index('ix_review_items_status_created_at', table_name='review_items')
    op.drop_table('review_items')
