"""add customers and tickets

The first business tables: a customer identified by email, and the support
tickets they raise.

Drafted with --autogenerate and then read through. Worth knowing when changing
it:

* ``status`` is VARCHAR + CHECK rather than a PostgreSQL ENUM type. Native
  enums need ALTER TYPE ... ADD VALUE to extend and are close to impossible to
  shrink; a check constraint is a one-line migration.
* The allowed status values are written out literally rather than imported from
  ``app.models``. A migration records the schema as it was at this revision; if
  it imported the enum it would silently change meaning the next time someone
  edited that enum.
* The foreign key is ON DELETE RESTRICT, so removing a customer cannot quietly
  destroy their ticket history.

Revision ID: 6dd90e1db408
Revises: 4708760a4840
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6dd90e1db408"
down_revision: str | Sequence[str] | None = "4708760a4840"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the customers and tickets tables."""
    op.create_table('customers',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_customers_email_lowercase')),
    sa.CheckConstraint('length(btrim(email)) > 0', name=op.f('ck_customers_email_not_blank')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customers')),
    sa.UniqueConstraint('email', name=op.f('uq_customers_email'))
    )
    op.create_table('tickets',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('customer_id', sa.Uuid(), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('open', 'pending', 'resolved', 'closed', name='status_valid', native_enum=False, create_constraint=True, length=20), server_default='open', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(btrim(body)) > 0', name=op.f('ck_tickets_body_not_blank')),
    sa.CheckConstraint('length(btrim(subject)) > 0', name=op.f('ck_tickets_subject_not_blank')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_tickets_customer_id_customers'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tickets'))
    )
    op.create_index('ix_tickets_created_at', 'tickets', ['created_at'], unique=False)
    op.create_index(op.f('ix_tickets_customer_id'), 'tickets', ['customer_id'], unique=False)
    op.create_index('ix_tickets_status_created_at', 'tickets', ['status', 'created_at'], unique=False)


def downgrade() -> None:
    """Drop both tables, children first."""
    op.drop_index('ix_tickets_status_created_at', table_name='tickets')
    op.drop_index(op.f('ix_tickets_customer_id'), table_name='tickets')
    op.drop_index('ix_tickets_created_at', table_name='tickets')
    op.drop_table('tickets')
    op.drop_table('customers')
