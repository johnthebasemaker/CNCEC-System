"""shared per-account login throttle — login_attempts

Revision ID: f3c81d5a97e2
Revises: e9f2a4c68b71
Create Date: 2026-08-05

The per-account failure budget (8 per 15 min, rule 10) lives in PROCESS memory,
so with N uvicorn workers the effective ceiling is N × 8 — the same caveat the
per-IP limiter carries. A single-box deploy behind one worker is unaffected;
anything larger silently multiplies the budget an attacker gets.

POSTGRES, NOT REDIS. The counter ticks a few times a minute at most. Postgres
is already deployed, already backed up, already in the runbook and already
holds the users table this protects; Redis would be a new service, a new
failure mode and a new thing to secure for a single small integer. An atomic
`INSERT … ON CONFLICT DO UPDATE … RETURNING` gives one shared count across
workers in a single round trip.

⚠️ IT STILL THROTTLES, NEVER LOCKS (rule 10). `window_start` rolls forward on
its own, a correct password deletes the row outright, and no administrator ever
has to clear anything. A per-account limit is a denial-of-service vector —
someone who knows a username can burn its budget deliberately — and that is
precisely why the recovery must be the passage of time rather than a support
ticket.

The row is the whole state: `username_lc` (the account, case-folded, so `Admin`
and `admin` share one budget), the start of the current window, and the count.
There is no history here and no audit value — `system_audit_log` already
records LOGIN_FAILED with the username.
"""
from alembic import op
import sqlalchemy as sa

revision = "f3c81d5a97e2"
down_revision = "e9f2a4c68b71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_attempts",
        sa.Column("username_lc", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("username_lc"),
    )


def downgrade() -> None:
    op.drop_table("login_attempts")
