"""add budget override audit columns to payment_orders

Revision ID: 20261104_01
Revises: 20261103_01
Create Date: 2026-11-04 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261104_01'
down_revision: Union[str, Sequence[str], None] = '20261103_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'payment_orders' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('payment_orders')]
        with op.batch_alter_table('payment_orders', schema=None) as batch_op:
            if 'budget_override_applied' not in existing_cols:
                batch_op.add_column(
                    sa.Column('budget_override_applied', sa.Boolean(), nullable=False, server_default='0')
                )
            if 'original_budget_amount' not in existing_cols:
                batch_op.add_column(
                    sa.Column('original_budget_amount', sa.Numeric(12, 2), nullable=True)
                )
            if 'budget_exceeded_amount' not in existing_cols:
                batch_op.add_column(
                    sa.Column('budget_exceeded_amount', sa.Numeric(12, 2), nullable=True)
                )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'payment_orders' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('payment_orders')]
        with op.batch_alter_table('payment_orders', schema=None) as batch_op:
            if 'budget_exceeded_amount' in existing_cols:
                batch_op.drop_column('budget_exceeded_amount')
            if 'original_budget_amount' in existing_cols:
                batch_op.drop_column('original_budget_amount')
            if 'budget_override_applied' in existing_cols:
                batch_op.drop_column('budget_override_applied')

