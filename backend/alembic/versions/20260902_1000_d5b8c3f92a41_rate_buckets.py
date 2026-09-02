"""rate_buckets — the cross-worker half of the four remaining in-process limiters

Revision ID: d5b8c3f92a41
Revises: c4a7e2b81f36
Create Date: 2026-09-02 10:00:00

WHY THIS TABLE, AND WHY NOT REDIS (operator ruling, 2026-09-02).

`login_attempts` (alembic f3c81d5a97e2) already made the per-ACCOUNT login
throttle true across workers, and its migration recorded the reasoning:
Postgres is already deployed, already backed up, already in the runbook and
already holds the users table it protects, while Redis would be a new service
and a new failure mode. That ruling was re-confirmed for Phase 10 rather than
assumed, and this table generalises the same mechanism to the four limiters
that were still per-process:

  · `rate_limit(n, w)`    — the per-IP FastAPI dependency on the public
                            auth endpoints
  · `check_bucket(key…)`  — identity-keyed budgets (one OTP allowance per
                            PHONE NUMBER, regardless of source IP)
  · `PenaltyBox`          — strike-based IP bans on the WhatsApp webhook
  · `_totp_failures`      — the per-account second-factor attempt budget

`deploy/Dockerfile.api` runs `uvicorn --workers 4`, so each of those had an
effective ceiling of 4x its configured limit. The last one is the most serious:
it is the ceiling on brute-forcing the SECOND FACTOR, and `_verify_totp` runs
at `valid_window=1`, which makes three 6-digit codes acceptable at any instant.

⚠️ THE IN-PROCESS CHECK IS NOT REMOVED. It runs first, costs nothing, and trips
inside a hot worker before any query happens; this row is what makes the ceiling
true across all four. Exactly the two-layer shape `login_attempts` established.

⚠️ AND IT FAILS OPEN (operator ruling, Q1.2). Every read and write here
swallows database errors and allows the request. A throttle that takes sign-in
down when its own storage hiccups is worse than the attack it prevents — the
same choice `assert_login_allowed_shared` already makes, stated again because
it is a security decision and not an implementation detail. Note this is the
OPPOSITE of the access matrix, which fails CLOSED (`nav_routes_check.mjs`): an
unknown route is refused, an unavailable throttle is not enforced.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd5b8c3f92a41'
down_revision = 'c4a7e2b81f36'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'rate_buckets',
        # The key carries its own namespace: "ip:1.2.3.4:/auth/login",
        # "totp:jsmith", "otp:+966…", "ban:1.2.3.4". One table rather than one
        # per limiter, because they share a window algorithm exactly and
        # differ only in what they count.
        sa.Column('bucket_key', sa.Text(), primary_key=True),
        sa.Column('window_start', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('hits', sa.Integer(), nullable=False,
                  server_default=sa.text('1')),
    )
    # The sweeper's predicate. Without it, cleaning expired buckets is a
    # sequential scan of every bucket the system has ever opened.
    op.create_index("ix_rate_buckets_window", "rate_buckets",
                    ["window_start"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_rate_buckets_window", table_name="rate_buckets")
    op.drop_table('rate_buckets')
