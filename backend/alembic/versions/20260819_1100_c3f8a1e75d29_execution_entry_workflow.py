"""execution_entry_workflow — Phase 5: SK → Supervisor → HOD

Three tables carrying the consumption workflow:
  · sme_execution_entry            the report and its state
  · sme_execution_entry_material   the store keeper's physical draw
  · sme_execution_entry_manpower   the supervisor's actual crew

Two shapes here are deliberate and easy to "fix" wrongly later:

1. `Lining_System_Code` is NOT NULL with a '' sentinel, not nullable. Surface
   prep (blasting, buffing) belongs to no lining system, so the column must be
   able to say "none" — but as the empty string, matching the ruling already
   taken for sme_recipe.Execution_Sub_Activity_Code in f1d3b7a24c60. Postgres
   treats NULLs as distinct, so a nullable column inside a key stops the key
   constraining, and every GROUP BY over it grows an untyped bucket that report
   renderers show as a blank row.

2. Every Bench_* column is a SNAPSHOT, written when the supervisor submits, not
   a join. `sme_manpower_norm` and `sme_recipe` are HOD-editable master data. A
   variance report that re-derived its benchmark would rewrite history the
   first time somebody corrected a productivity figure — last quarter's 12%
   overrun quietly becoming 4%, with no edit to the entry and nothing to point
   at. Norm_ID says WHICH benchmark applied; the Bench_* columns say what it
   SAID at the time.

Revision ID: c3f8a1e75d29
Revises: a7e2c9d41b83
Create Date: 2026-08-19 11:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3f8a1e75d29"
down_revision: Union[str, None] = "a7e2c9d41b83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sme_execution_entry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Site_ID", sa.Text(), nullable=False),
        sa.Column("Entry_No", sa.Text(), nullable=False),
        sa.Column("Work_Date", sa.Text(), nullable=False),
        sa.Column("Equipment_Tag_No", sa.Text(), nullable=False),
        sa.Column("Lining_System_Code", sa.Text(),
                  server_default=sa.text("''"), nullable=False),
        sa.Column("Execution_Sub_Activity_Code", sa.Text(), nullable=False),
        sa.Column("Variant_Key", sa.Text(), server_default=sa.text("''"),
                  nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'DRAFT_SK'"),
                  nullable=False),
        sa.Column("sk_username", sa.Text(), nullable=True),
        sa.Column("sk_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("Actual_SQM", sa.Float(), nullable=True),
        sa.Column("supervisor_username", sa.Text(), nullable=True),
        sa.Column("supervisor_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("Material_Variance_Reason", sa.Text(), nullable=True),
        sa.Column("Manpower_Variance_Reason", sa.Text(), nullable=True),
        sa.Column("Norm_ID", sa.Integer(), nullable=True),
        sa.Column("Bench_Crew_Size", sa.Float(), nullable=True),
        sa.Column("Bench_Hours_Per_Shift", sa.Float(), nullable=True),
        sa.Column("Bench_Manhours_Per_Shift", sa.Float(), nullable=True),
        sa.Column("Bench_Productivity_Per_Shift", sa.Float(), nullable=True),
        sa.Column("Bench_SQM_Per_Hour_Per_Person", sa.Float(), nullable=True),
        sa.Column("Bench_Snapshot_At", sa.DateTime(), nullable=True),
        sa.Column("hod_username", sa.Text(), nullable=True),
        sa.Column("hod_decided_at", sa.DateTime(), nullable=True),
        sa.Column("HOD_Edit_Justification", sa.Text(), nullable=True),
        sa.Column("hod_edited", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("Reject_Reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["Norm_ID"], ["sme_manpower_norm.id"],
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Site_ID", "Entry_No"),
    )
    op.create_index("ix_sme_exec_entry_status", "sme_execution_entry",
                    ["Site_ID", "status"])
    op.create_table(
        "sme_execution_entry_material",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Entry_ID", sa.Integer(), nullable=False),
        sa.Column("Material_Code", sa.Text(), nullable=False),
        sa.Column("SAP_Code", sa.Text(), server_default=sa.text("''"),
                  nullable=False),
        sa.Column("Actual_Qty", sa.Float(), server_default=sa.text("0"),
                  nullable=False),
        sa.Column("UOM", sa.Text(), nullable=True),
        sa.Column("Lot_No", sa.Text(), nullable=True),
        sa.Column("Bench_For_1_SQM", sa.Float(), nullable=True),
        sa.Column("Original_Qty", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["Entry_ID"], ["sme_execution_entry.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Entry_ID", "Material_Code", "SAP_Code"),
    )
    op.create_table(
        "sme_execution_entry_manpower",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Entry_ID", sa.Integer(), nullable=False),
        sa.Column("Role_Code", sa.Text(), nullable=False),
        sa.Column("Headcount", sa.Float(), server_default=sa.text("0"),
                  nullable=False),
        sa.Column("Hours", sa.Float(), server_default=sa.text("0"),
                  nullable=False),
        sa.Column("Bench_Headcount", sa.Float(), nullable=True),
        sa.Column("Original_Headcount", sa.Float(), nullable=True),
        sa.Column("Original_Hours", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["Entry_ID"], ["sme_execution_entry.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Entry_ID", "Role_Code"),
    )


def downgrade() -> None:
    op.drop_table("sme_execution_entry_manpower")
    op.drop_table("sme_execution_entry_material")
    op.drop_index("ix_sme_exec_entry_status", table_name="sme_execution_entry")
    op.drop_table("sme_execution_entry")
