"""create price_snapshots table

Revision ID: 20261103_01
Revises: 
Create Date: 2026-11-03 21:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261103_01'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'price_snapshots' not in existing_tables:
        op.create_table(
            'price_snapshots',
            sa.Column('id', sa.String(length=64), nullable=False),
            sa.Column('product_id', sa.String(length=64), nullable=False),
            sa.Column('retailer_name', sa.String(length=128), nullable=False),
            sa.Column('provider_offer_id', sa.String(length=128), nullable=True),
            sa.Column('price', sa.Float(), nullable=False),
            sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'),
            sa.Column('availability', sa.String(length=64), nullable=False, server_default='IN_STOCK'),
            sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('is_synthetic', sa.Boolean(), nullable=False, server_default='0'),
            sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('price_snapshots', schema=None) as batch_op:
            batch_op.create_index('ix_price_snapshots_product_id', ['product_id'], unique=False)
            batch_op.create_index('ix_price_snapshots_retailer_name', ['retailer_name'], unique=False)
            batch_op.create_index('ix_price_snapshots_provider_offer_id', ['provider_offer_id'], unique=False)
            batch_op.create_index('ix_price_snapshots_recorded_at', ['recorded_at'], unique=False)
            batch_op.create_index('ix_price_snapshots_product_currency', ['product_id', 'currency'], unique=False)
            batch_op.create_index('ix_price_snapshots_product_retailer', ['product_id', 'retailer_name'], unique=False)
            batch_op.create_index('ix_price_snapshots_lookup', ['product_id', 'currency', 'recorded_at'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'price_snapshots' in existing_tables:
        with op.batch_alter_table('price_snapshots', schema=None) as batch_op:
            batch_op.drop_index('ix_price_snapshots_lookup')
            batch_op.drop_index('ix_price_snapshots_product_retailer')
            batch_op.drop_index('ix_price_snapshots_product_currency')
            batch_op.drop_index('ix_price_snapshots_recorded_at')
            batch_op.drop_index('ix_price_snapshots_provider_offer_id')
            batch_op.drop_index('ix_price_snapshots_retailer_name')
            batch_op.drop_index('ix_price_snapshots_product_id')

        op.drop_table('price_snapshots')
