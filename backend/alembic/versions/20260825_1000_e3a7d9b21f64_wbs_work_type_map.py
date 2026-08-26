"""WBS by work type — one canonical list, one mapping, one resolution order

ONE TABLE, NOT TWO. The operator asked for two things (ruling Q14: a strict
`Work_Type` dropdown the HOD manages; ruling Q16: a WBS number per work type
per site) and they are the same row. A work type IS a row here; giving it a
`WBS_Number` is what maps it. `WBS_Number` is therefore NULLABLE — "this work
type exists and has no WBS yet" is an ordinary, expected state, not a defect.
Two tables would have made every screen a join and every insert a decision
about which half to write first.

⚠️ THE UNIQUE KEY IS THE *NORMALISED* SPELLING, NOT THE DISPLAYED ONE. The live
ledger holds 35 distinct `Work_Type` strings over 1,674 rows, and four of those
pairs differ only in case:

    civil / Civil      coating / Coating      In yard / In Yard      others / Others

Keyed on the raw text, `Civil` and `civil` would take different WBS numbers and
nobody would ever see why the report split. `Work_Type_Norm` (lower + trimmed +
whitespace-collapsed) is the identity; `Work_Type` is only how it is spelled
back to a human. Near-duplicates that are NOT case collisions — `Arrangement` vs
`Site Arrangement`, `Blasting` vs `Sweep blast` — normalisation cannot merge,
and deliberately does not try: those are the HOD's judgement, made in the UI.

⚠️ NOTHING IS SEEDED, AND THAT IS THE POINT. Both gates in this system
(`entry_docs.assert_wbs` and now `assert_work_type`) are CONDITIONAL: they do
nothing until a site has active rows. An empty table therefore means the app
behaves exactly as it did yesterday, and the HOD turns the rule on for their
site when they are ready. Seeding the 35 historical spellings would have
enshrined `civil` AND `Civil` as two permanent, blessed options — the precise
mess this table exists to end. `/hod/site-config/work-types/suggestions` offers
them one at a time instead, already merged by normalised spelling and carrying
their usage counts, so adopting one is a decision rather than a default.

Revision ID: e3a7d9b21f64
Revises: c7e1a4b92d63
Create Date: 2026-08-25 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3a7d9b21f64"
down_revision: Union[str, None] = "c7e1a4b92d63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wbs_work_type_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Site_ID", sa.Text(), nullable=False),
        # What a human sees and what gets STORED on the ledger row. The HOD
        # chooses the casing once, here, and every entry form spells it the
        # same way from then on.
        sa.Column("Work_Type", sa.Text(), nullable=False),
        # The identity. Written by the API, never by a form.
        sa.Column("Work_Type_Norm", sa.Text(), nullable=False),
        # NULL = a work type with no WBS yet. Not a foreign key: `wbs_master`
        # rows can be closed, and a closed WBS on a historical mapping is
        # information rather than corruption.
        sa.Column("WBS_Number", sa.Text(), nullable=True),
        sa.Column("Description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"),
                  nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # One row per work type per site — ruling Q16. The same work type may
        # carry a DIFFERENT WBS at a different site, which is why Site_ID is
        # half the key rather than a filter.
        sa.UniqueConstraint("Site_ID", "Work_Type_Norm",
                            name="uq_wbs_work_type_site_norm"),
    )
    # The resolver reads (Site_ID, Work_Type_Norm, status) on every staged
    # issue. The unique constraint already covers the first two; status is
    # what turns a scan into a lookup.
    op.create_index("ix_wbs_work_type_active", "wbs_work_type_map",
                    ["Site_ID", "status"])


def downgrade() -> None:
    op.drop_index("ix_wbs_work_type_active", table_name="wbs_work_type_map")
    op.drop_table("wbs_work_type_map")
