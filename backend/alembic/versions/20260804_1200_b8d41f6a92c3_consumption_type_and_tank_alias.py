"""consumption.Item_Type + sme_tank_alias (Surface-Shield routing)

Revision ID: b8d41f6a92c3
Revises: e7c3b95a41d2
Create Date: 2026-08-04

The 2026-08-04 `CNCEC_Inventory.xlsx` adds a `type` column to the Consumption
Log — populated on all 1,110 rows, and the only field that says WHICH
programme a consumption belongs to:

    R/L Consumables 541 · BR CC PU Tools 292 · Surface Shield 103 · Safety 76
    R/L Tools 29 · Electrical Items 44 · EQUIPMENTS/TOOLS 15 · Others 7
    Blasting 1 · Office 1 · QC 1

`Item_Type` PERSISTS that classification rather than re-deriving it from
`inventory.Category` at read time, because a material that is recategorised
later would otherwise retroactively rewrite what past consumption "was".

`sme_tank_alias` exists because the workbook's `Tank No.` cannot be matched to
`sme_equipment` automatically. Measured over the 103 Surface-Shield rows:

    TNK-091 39 · J0091 25 · J-0091 22 · J091 7 · J 0091 4
    J092 2 · Sample Plate 1 · others 1

Four of those normalise cleanly to `J091`, which exists. **`TNK-091` does
not** — it suffix-matches BOTH `522-8J10-TNK-091` (TRAIN J) and
`522-8k10-TNK-091` (TRAIN K), two different vessels on two different trains,
and it is the single largest bucket at 39 rows. A fuzzy match there would post
real consumption against the wrong train and look entirely plausible in every
report afterwards.

So the sync auto-maps only aliases whose normalised form matches EXACTLY ONE
equipment tag, registers everything else as `unresolved`, and an operator
resolves those in the SME → Tank Aliases screen. Nothing is ever guessed and
nothing is ever dropped.

`Item_Type` is a plain nullable column: `plan_ledger`'s three-tier reconcile is
unaffected, and the ledger keeps having NO unique constraint (rule 3 — the same
(date, SAP, quantity) line can legitimately repeat).
"""
from alembic import op
import sqlalchemy as sa

revision = "b8d41f6a92c3"
down_revision = "e7c3b95a41d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("consumption", sa.Column("Item_Type", sa.Text(), nullable=True))

    op.create_table(
        "sme_tank_alias",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Site_ID", sa.Text(), nullable=False),
        # `alias_raw` keeps the operator's spelling verbatim so the screen can
        # show what the workbook actually said; `alias_norm` is what matching
        # uses (upper-cased, every space/hyphen/underscore removed).
        sa.Column("alias_raw", sa.Text(), nullable=False),
        sa.Column("alias_norm", sa.Text(), nullable=False),
        sa.Column("Equipment_Tag_No", sa.Text(), nullable=True),
        # unresolved → nothing (or >1 thing) matched, rows import unassigned
        # mapped     → resolved, either automatically or by an operator
        # ignored    → deliberately not an equipment ("To site", "House Keeping")
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'unresolved'")),
        sa.Column("match_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("row_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Site_ID", "alias_norm",
                            name="uq_sme_tank_alias_site_norm"),
    )


def downgrade() -> None:
    op.drop_table("sme_tank_alias")
    op.drop_column("consumption", "Item_Type")
