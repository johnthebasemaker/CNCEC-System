"""users.email — a per-recipient address for the weekly Executive Summary

Revision ID: a71e93b4c2f8
Revises: f3c81d5a97e2
Create Date: 2026-08-05

Every email the system sends today goes to ONE configured inbox
(`emailer.logistics_to()`), because `users` has never carried an address. That
is fine for "tell logistics something happened" and wrong for "send each HOD
their own site's report", which is what the weekly Executive Summary needs.

Nullable, and NOT unique: two people legitimately share an address (a shift
account, a departmental mailbox), and a UNIQUE here would reject the second one
at user-creation time for no benefit.

Until an address is filled in, `weekly_report` falls back to the configured
exec inbox, so the feature delivers on day one with no data entry and gets
more precise as addresses are added.
"""
from alembic import op
import sqlalchemy as sa

revision = "a71e93b4c2f8"
down_revision = "f3c81d5a97e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "email")
