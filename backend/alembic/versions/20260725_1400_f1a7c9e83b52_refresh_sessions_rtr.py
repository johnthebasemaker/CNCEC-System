"""refresh_sessions — Refresh Token Rotation (RTR) with token families

Supersedes auth_sessions (kept for audit until its rows expire; no new
writes). One row per refresh token; a login opens a token *family* and every
rotation appends a row (same family_id, new jti). Replaying a revoked token
revokes the whole family — other families (other devices) survive.
client_type drives the TTL: 'web' 7 days, 'native' (Tauri/Capacitor) 90 days.

Also revokes gi_ai_ro's SELECT on the new table when the role exists — the
provisioning script's ALTER DEFAULT PRIVILEGES would otherwise auto-expose
it (the script's REVOKE list is updated too, for full reloads).

Revision ID: f1a7c9e83b52
Revises: c7d4e8f19a25
Create Date: 2026-07-25 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a7c9e83b52'
down_revision: Union[str, None] = 'c7d4e8f19a25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'refresh_sessions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('username', sa.Text(), nullable=False),
        sa.Column('family_id', sa.Uuid(), nullable=False),
        sa.Column('refresh_token_jti', sa.Text(), nullable=False, unique=True),
        sa.Column('client_type', sa.Text(), nullable=False,
                  server_default=sa.text("'web'")),
        sa.Column('created_at', sa.DateTime(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('is_revoked', sa.Boolean(), nullable=False,
                  server_default=sa.text('FALSE')),
        sa.Column('revoked_at', sa.DateTime()),
        sa.Column('revoke_reason', sa.Text()),
        sa.Column('replaced_by', sa.Uuid()),
    )
    op.create_index('ix_refresh_sessions_user_id', 'refresh_sessions', ['user_id'])
    op.create_index('ix_refresh_sessions_username', 'refresh_sessions', ['username'])
    op.create_index('ix_refresh_sessions_family_id', 'refresh_sessions', ['family_id'])
    # Token material metadata must never be readable by the AI role.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gi_ai_ro') THEN
                REVOKE SELECT ON refresh_sessions FROM gi_ai_ro;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_index('ix_refresh_sessions_family_id', table_name='refresh_sessions')
    op.drop_index('ix_refresh_sessions_username', table_name='refresh_sessions')
    op.drop_index('ix_refresh_sessions_user_id', table_name='refresh_sessions')
    op.drop_table('refresh_sessions')
