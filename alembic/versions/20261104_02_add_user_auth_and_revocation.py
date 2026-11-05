"""add user authentication and token revocation tables

Revision ID: 20261104_02
Revises: 20261104_01
Create Date: 2026-11-04 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261104_02'
down_revision: Union[str, Sequence[str], None] = '20261104_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # 1. Create users table if not exists
    if 'users' not in existing_tables:
        op.create_table(
            'users',
            sa.Column('id', sa.String(length=64), primary_key=True),
            sa.Column('email', sa.String(length=255), nullable=False),
            sa.Column('hashed_password', sa.String(length=255), nullable=False),
            sa.Column('name', sa.String(length=128), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_users_email', 'users', ['email'], unique=True)

    # 2. Create revoked_tokens table if not exists
    if 'revoked_tokens' not in existing_tables:
        op.create_table(
            'revoked_tokens',
            sa.Column('id', sa.String(length=64), primary_key=True),
            sa.Column('jti', sa.String(length=64), nullable=False),
            sa.Column('user_id', sa.String(length=64), sa.ForeignKey('users.id', name='fk_revoked_tokens_user_id', ondelete='CASCADE'), nullable=False),
            sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_revoked_tokens_jti', 'revoked_tokens', ['jti'], unique=True)
        op.create_index('ix_revoked_tokens_user_id', 'revoked_tokens', ['user_id'])
        op.create_index('ix_revoked_tokens_jti_expires', 'revoked_tokens', ['jti', 'expires_at'])

    # 3. Add user_id and session_token to shopping_sessions
    if 'shopping_sessions' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('shopping_sessions')]
        with op.batch_alter_table('shopping_sessions', schema=None) as batch_op:
            if 'user_id' not in existing_cols:
                batch_op.add_column(
                    sa.Column('user_id', sa.String(length=64), sa.ForeignKey('users.id', name='fk_shopping_sessions_user_id', ondelete='SET NULL'), nullable=True)
                )
                batch_op.create_index('ix_shopping_sessions_user_id', ['user_id'])
            if 'session_token' not in existing_cols:
                batch_op.add_column(
                    sa.Column('session_token', sa.String(length=64), nullable=True)
                )
                batch_op.create_index('ix_shopping_sessions_session_token', ['session_token'])

    # 4. Add user_id to payment_orders
    if 'payment_orders' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('payment_orders')]
        with op.batch_alter_table('payment_orders', schema=None) as batch_op:
            if 'user_id' not in existing_cols:
                batch_op.add_column(
                    sa.Column('user_id', sa.String(length=64), sa.ForeignKey('users.id', name='fk_payment_orders_user_id', ondelete='SET NULL'), nullable=True)
                )
                batch_op.create_index('ix_payment_orders_user_id', ['user_id'])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'payment_orders' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('payment_orders')]
        with op.batch_alter_table('payment_orders', schema=None) as batch_op:
            if 'user_id' in existing_cols:
                batch_op.drop_index('ix_payment_orders_user_id')
                batch_op.drop_column('user_id')

    if 'shopping_sessions' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('shopping_sessions')]
        with op.batch_alter_table('shopping_sessions', schema=None) as batch_op:
            if 'session_token' in existing_cols:
                batch_op.drop_index('ix_shopping_sessions_session_token')
                batch_op.drop_column('session_token')
            if 'user_id' in existing_cols:
                batch_op.drop_index('ix_shopping_sessions_user_id')
                batch_op.drop_column('user_id')

    if 'revoked_tokens' in existing_tables:
        op.drop_table('revoked_tokens')

    if 'users' in existing_tables:
        op.drop_table('users')
