"""procurement locks — the PR number registry and idempotency keys

TWO TABLES, EACH CLOSING A HOLE THAT LOOKED LIKE NOTHING.

**1. `pr_registry` — the PR number, once.**
`_next_pr_number()` read the newest row with today's prefix and added one:

    last = SELECT "PR_Number" ... LIKE 'PR-20260822-%' ORDER BY id DESC LIMIT 1
    nxt  = int(last.split('-')[-1]) + 1

Read-then-write with no lock, and `pr_master."PR_Number"` cannot be unique
because a PR is MANY LINES. Two HODs creating a PR in the same second both read
`0003` and both write `0004`, and from that moment two different requests are
one PR to every query in the system — the Logistics queue, the PO, the audit
trail. Nothing raises; it just quietly becomes wrong.

A PR number needs a table where it appears ONCE so it can carry a primary key,
which is what this is. `_next_pr_number` now generates, INSERTs, and retries on
conflict: the DATABASE decides who got the number, not whoever read last.

**2. `procurement_idempotency` — a retry is not a second order.**
A double-clicked Submit, a flaky-network retry, a stale tab — all send the same
request twice, and for four actions in this chain the second one is a second
purchase request, a second notification to Logistics, or a second warehouse
told to expect the same goods. The key is claimed BEFORE the work and filled in
after, so two concurrent requests with one key serialise on the primary key
rather than racing.

⚠️ NO `uq_po_per_pr`. OPERATOR RULING 2026-08-20 (Q7): a PR may legitimately
carry SEVERAL POs — partial fulfilment splits one request across vendors or
deliveries. The uniqueness wanted is `PR_Number` unique in the registry and
`PO_Number` unique in `purchase_orders` (which it already is), and nothing more.
Constraining one PO per PR would have made partial fulfilment unrepresentable.

Revision ID: a9f2c6b40d18
Revises: d4b8c1e63a27
Create Date: 2026-08-22 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9f2c6b40d18"
down_revision: Union[str, None] = "d4b8c1e63a27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pr_registry",
        sa.Column("PR_Number", sa.Text(), nullable=False),
        sa.Column("Site_ID", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("PR_Number"),
    )
    op.create_table(
        "procurement_idempotency",
        sa.Column("idem_key", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        # sha256 of the canonicalised body. A retry must carry the SAME request;
        # the same key with a different body is a client bug, not a retry, and
        # replaying the first answer would hide it.
        sa.Column("body_hash", sa.Text(), nullable=False),
        # '' while the work is in flight. A concurrent second request with the
        # same key sees the claim and is told to wait rather than being handed
        # an answer that does not exist yet.
        sa.Column("result_json", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("idem_key"),
    )
    op.create_index("ix_procurement_idem_action", "procurement_idempotency",
                    ["action", "created_at"])

    data_upgrade(op.get_bind())


def data_upgrade(conn) -> None:
    """DATA step — see cutover_migrate.run_data_migrations.

    Backfills `pr_registry` from the PR numbers already in `pr_master`.

    ⚠️ IT SURVEYS FIRST AND REFUSES. `PR_Number` is the registry's primary key,
    so a number issued at two different sites cannot be represented — and the
    old generator could produce exactly that, since it looked only at the
    newest row and not at the site. If any exist the migration RAISES with the
    list rather than half-applying: which of two real purchase requests keeps
    the number is a commercial decision, not something a migration may pick.

    Idempotent: the insert is ON CONFLICT DO NOTHING, so re-running adds
    nothing.
    """
    clashes = conn.execute(sa.text(
        'SELECT "PR_Number", string_agg(DISTINCT COALESCE("Site_ID", \'HQ\'), \', \') '
        "FROM pr_master WHERE COALESCE(\"PR_Number\", '') <> '' "
        'GROUP BY "PR_Number" '
        'HAVING COUNT(DISTINCT COALESCE("Site_ID", \'HQ\')) > 1')).fetchall()
    if clashes:
        listed = "; ".join(f"{r[0]} at {r[1]}" for r in clashes[:20])
        more = f" (+{len(clashes) - 20} more)" if len(clashes) > 20 else ""
        raise RuntimeError(
            f"{len(clashes)} PR number(s) are used at more than one site and "
            f"cannot enter a registry keyed on the number alone: {listed}{more}. "
            f"Rename one side (HOD Portal -> Purchase Requests -> rename, which "
            f"only works while a PR is still a draft) or force-close the "
            f"duplicate, then re-run. Nothing has been written.")

    conn.execute(sa.text(
        'INSERT INTO pr_registry ("PR_Number", "Site_ID", created_by) '
        'SELECT "PR_Number", MIN(COALESCE("Site_ID", \'HQ\')), \'alembic-backfill\' '
        "FROM pr_master WHERE COALESCE(\"PR_Number\", '') <> '' "
        'GROUP BY "PR_Number" '
        'ON CONFLICT ("PR_Number") DO NOTHING'))
    n = conn.execute(sa.text("SELECT COUNT(*) FROM pr_registry")).scalar() or 0
    print(f"  · pr_registry holds {n} PR number(s)")


def downgrade() -> None:
    op.drop_index("ix_procurement_idem_action",
                  table_name="procurement_idempotency")
    op.drop_table("procurement_idempotency")
    op.drop_table("pr_registry")
