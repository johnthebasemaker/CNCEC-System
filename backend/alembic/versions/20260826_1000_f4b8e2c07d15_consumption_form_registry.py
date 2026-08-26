"""the printed consumption form — a registry, not just a PDF

WHY A TABLE AT ALL, WHEN THE PDF COULD BE GENERATED AND FORGOTTEN.

Three things only a registry can do, and slice 9d needs all three:

  1. **A photograph of the same sheet twice is a duplicate, and nothing else
     can tell.** Two supervisors photographing one form, or one supervisor
     retrying on a bad signal, produce byte-different images of identical
     paper. `Form_UUID` — stamped into the QR — is the only handle that says
     "this is the sheet you already filed", and `consumed_entry_id` is what
     makes the second upload a clear refusal instead of a second consumption.

  2. **Paper outlives the recipe it was printed from.** A form printed today
     lists LSC8's four Cumicrete components as rows 1–4. If someone edits
     `sme_recipe` next week — adds a component, reorders, changes a SAP code —
     a sheet already in somebody's pocket now maps row 3 to a different
     material, and the OCR would read the handwriting into the wrong line with
     no way to notice. `Recipe_Fingerprint` is a hash of exactly what was
     printed; slice 9d compares it on upload and refuses a stale sheet rather
     than silently mis-filing it.

  3. **`Row_Count` is a free integrity check.** The vision model returns rows;
     the form knows how many boxes it printed. A mismatch is a torn page, a
     cropped photo, or a hallucinated row — all worth catching before a human
     reviews numbers that look plausible.

⚠️ NOTHING HERE IS A FOREIGN KEY TO `sme_recipe`. The form records what was on
the paper AT PRINT TIME, which is precisely the thing a live join would destroy:
resolving the recipe again at upload would show today's materials against
yesterday's handwriting and call it agreement.

⚠️ `Execution_Sub_Activity_Code` MAY BE ''. A form generated for a whole lining
system carries every material across all of its sub-activities (LSC1 has five
across three); one generated for a single sub-activity carries only that one.
Empty string, not NULL — the same rule `sme_execution_entry` already follows
for a system-agnostic entry, so a GROUP BY over the column never drops rows.

Revision ID: f4b8e2c07d15
Revises: e3a7d9b21f64
Create Date: 2026-08-26 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4b8e2c07d15"
down_revision: Union[str, None] = "e3a7d9b21f64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sme_consumption_form",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # The QR payload's identity half, and the duplicate-upload guard.
        sa.Column("Form_UUID", sa.Text(), nullable=False),
        sa.Column("Site_ID", sa.Text(), nullable=False),
        sa.Column("Lining_System_Code", sa.Text(), nullable=False),
        # '' = every sub-activity of the system. See the docstring.
        sa.Column("Execution_Sub_Activity_Code", sa.Text(),
                  server_default=sa.text("''"), nullable=False),
        # What was actually printed, so a later upload can prove the paper and
        # the database still agree.
        sa.Column("Recipe_Fingerprint", sa.Text(), nullable=False),
        sa.Column("Row_Count", sa.Integer(), server_default=sa.text("0"),
                  nullable=False),
        # open → consumed (a photo was accepted) or void (printed in error).
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"),
                  nullable=False),
        sa.Column("consumed_entry_id", sa.Integer(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_by_role", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # The lookup slice 9d performs on every upload, and the constraint that
        # makes a replayed QR impossible rather than merely unlikely.
        sa.UniqueConstraint("Form_UUID", name="uq_consumption_form_uuid"),
    )
    # "What has this site printed and not yet filed?" — the supervisor's own
    # question, and the one an HOD asks when paper goes missing.
    op.create_index("ix_consumption_form_site_status", "sme_consumption_form",
                    ["Site_ID", "status"])


def downgrade() -> None:
    op.drop_index("ix_consumption_form_site_status",
                  table_name="sme_consumption_form")
    op.drop_table("sme_consumption_form")
