"""sme_equipment SQM override — the app wins over the workbook

Revision ID: c1a72e5b83d9
Revises: b8d41f6a92c3
Create Date: 2026-08-04

Operators need to correct a tank's surface area from the UI without waiting for
`Equipment.xlsx` to catch up. Two things then collide:

  * the ordinary sync upserts `sme_equipment` from the workbook, and
  * `--sme-reseed` DELETEs the site's rows and rebuilds them (rule 4),

either of which silently reverts the correction — and because SQM drives
DEMAND, the operator would see their number quietly disappear from every
report a week later with nothing to point at.

**Ruling 2026-08-04: THE APP WINS.** A UI edit records `SQM_Override` beside
the workbook's `Surface_Area_SQM`. Every sync path re-applies the override
afterwards and REPORTS the divergence rather than resolving it silently:

    ⚠ 3 equipment row(s) keep an operator SQM override (workbook differs):
        522-8J10-TNK-091 / 1  workbook 247.0 → override 251.5

Storing the override as its own column (rather than just writing
`Surface_Area_SQM` and hoping) is what makes the divergence *visible*: without
it there is no way to tell an operator's deliberate correction from a stale
workbook value, and "preserve edits" degenerates into "never sync again".

Clearing an override is an explicit UI action (set it back to NULL), after
which the workbook resumes ownership of that row.
"""
from alembic import op
import sqlalchemy as sa

revision = "c1a72e5b83d9"
down_revision = "b8d41f6a92c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sme_equipment",
                  sa.Column("SQM_Override", sa.Float(), nullable=True))
    op.add_column("sme_equipment",
                  sa.Column("SQM_Override_By", sa.Text(), nullable=True))
    op.add_column("sme_equipment",
                  sa.Column("SQM_Override_At", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("sme_equipment", "SQM_Override_At")
    op.drop_column("sme_equipment", "SQM_Override_By")
    op.drop_column("sme_equipment", "SQM_Override")
