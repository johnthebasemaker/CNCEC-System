"""A QC rejection produces a Return No, and a receipt knows when it was posted

Revision ID: c7a93e5d2b18
Revises: b4f21c8ea9d7
Create Date: 2026-08-13

TWO CHANGES, BOTH ABOUT THE RETURN FORM.

1. `receipts.posted_at` — WHY THE DROPDOWN LOOKED EMPTY

`/entry/return-sources` offers receipts from the last 30 days, and it measured
those 30 days against `receipts."Date"` — which is the DELIVERY date the store
keeper typed, copied off the vendor's paperwork. That was a fair proxy while
people typed today's date. It stopped being one as soon as receipts started
arriving through the DN flow carrying the carrier's date: goods received this
morning, dated six weeks ago on the document, are outside a 30-day window that
is trying to express "recent" and is measuring the wrong thing.

`posted_at` records when the row entered the LEDGER, which is what the rule
actually means. The window now accepts either.

⚠️ EXISTING ROWS ARE LEFT NULL ON PURPOSE, and the column is added without a
default for exactly that reason. `ADD COLUMN ... DEFAULT CURRENT_TIMESTAMP`
backfills every existing row with the time of the migration — which would
declare all 632 historical receipts to have been posted today and drop every
one of them into the return dropdown. The default is attached afterwards so it
applies only to rows inserted from now on. A NULL means "we do not know when
this was posted", and the query falls back to `Date` for those, which is the
honest answer rather than a manufactured one.

2. `qc_inspections.return_no` — THE HANDOFF THAT WAS A CONVERSATION

When QC rejected a quantity, the store keeper was told "12 of 30 approved" and
left to build the return themselves: find the material, find the source
receipt, retype the quantity, retype the reason. Every one of those is a
chance to return the wrong thing, and none of it was linked back to the
inspection that caused it.

The rejection now mints a Return No. It is a human-quotable handle — the QC
reads it out, the store keeper types it into the return form, and the form
fills itself from the inspection. UNIQUE, because the whole point is that it
identifies exactly one rejection; two returns against one Return No would
double-count the rejected quantity out of stock.
"""
from alembic import op
import sqlalchemy as sa

revision = "c7a93e5d2b18"
down_revision = "b4f21c8ea9d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # See the docstring: no default on ADD, so historical rows stay NULL.
    op.add_column("receipts", sa.Column("posted_at", sa.DateTime(), nullable=True))
    op.execute("ALTER TABLE receipts ALTER COLUMN posted_at "
               "SET DEFAULT CURRENT_TIMESTAMP")

    op.add_column("qc_inspections", sa.Column("return_no", sa.Text(), nullable=True))
    op.add_column("qc_inspections", sa.Column("return_posted_id", sa.Integer(), nullable=True))
    # Partial, so the many NULLs (every approved inspection) do not collide.
    op.execute('CREATE UNIQUE INDEX ux_qc_inspection_return_no '
               'ON qc_inspections (return_no) WHERE return_no IS NOT NULL')


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_qc_inspection_return_no")
    op.drop_column("qc_inspections", "return_posted_id")
    op.drop_column("qc_inspections", "return_no")
    op.drop_column("receipts", "posted_at")
