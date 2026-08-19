"""surface_prep_progress — Phase 6: prep area is not lining area

⚠️ THE WHOLE POINT: blasting 100 m² of a tank is NOT 100 m² of lining done.
The surface is merely ready to be lined. Folding prep into
`sme_sqm_progress.Done_SQM` would report a vessel as part-lined the moment it
was cleaned, and every downstream figure reads that column — Completion_Pct,
SQM_Achievable_Now, the shortfall and the buy list.

So prep gets its own ledger, keyed on the SUB-ACTIVITY rather than a lining
system, because a lining system is exactly what surface prep does not have.

Revision ID: e5b2d7c94a16
Revises: c3f8a1e75d29
Create Date: 2026-08-19 16:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5b2d7c94a16"
down_revision: Union[str, None] = "c3f8a1e75d29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sme_surface_prep_progress",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Site_ID", sa.Text(), nullable=False),
        sa.Column("Equipment_Tag_No", sa.Text(), nullable=False),
        sa.Column("Execution_Sub_Activity_Code", sa.Text(), nullable=False),
        sa.Column("Variant_Key", sa.Text(), server_default=sa.text("''"),
                  nullable=False),
        sa.Column("Activity", sa.Text(), nullable=True),
        sa.Column("Done_SQM", sa.Float(), server_default=sa.text("0"),
                  nullable=False),
        sa.Column("Entry_Count", sa.Integer(), server_default=sa.text("0"),
                  nullable=False),
        sa.Column("Last_Entry_No", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Site_ID", "Equipment_Tag_No",
                            "Execution_Sub_Activity_Code", "Variant_Key",
                            name="sme_surface_prep_identity_key"),
    )


def downgrade() -> None:
    op.drop_table("sme_surface_prep_progress")
