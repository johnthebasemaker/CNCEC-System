"""QSEP slice 5 — employee identity, site movements, and the 30816 backfill

Revision ID: d2f84b19e57c
Revises: c9e35a71d4b6
Create Date: 2026-08-10

THE FINDING THIS MIGRATION EXISTS FOR

There are two employee registries and nothing joins them:

  employees      ID_Number UNIQUE (GLOBAL)         — SMR worker validation,
                                                     QR badges, master export
  mh_employees   UNIQUE (Site_ID, Employee_Code)   — the attendance roster

`POST /mh/import` — the HOD's attendance workbook, and the only bulk employee
upload in the product — writes `mh_employees` ONLY. So a worker imported from
the roster could not be named on a supervisor material request: `create_smr`
looks them up in `employees` and answers "worker not in employee master".

`mh_employees.linked_id_number` has existed since the baseline migration
(ad1a8cc8e964:277) and is **never written or read anywhere in the
repository**. It is the intended join key and it is dead. This programme
starts writing it — no schema change needed for that part.

RULING R1, ACCEPTED 2026-08-09: `employees.ID_Number` IS THE PERSON.
`mh_employees` is a per-site EMPLOYMENT RECORD.

That is not a stylistic preference. `mh_employees` is keyed on
(Site_ID, Employee_Code), so a transfer NECESSARILY creates a second row.
Anything hung off it forks on transfer — which is precisely what "PPE history
carries over" forbids. Hence ppe_distributions.employee_id_number.

THE THREE ADDED COLUMNS carry what the attendance workbook already supplies
(Designation / Worker_Type / Company) so the two registries stop disagreeing
about the same person once the import writes both.

THE BACKFILL, AND WHY IT IS ONE ROW AND NOT A HEURISTIC

`employees` holds 2 rows and one of them (ID_Number 30816, "Johnson Andrew",
Store) has Site_ID = ''. The SMR worker check is
`(w[2] or "") != site_id`, so that person is rejected for EVERY site — a
silent, invisible failure with no error anywhere pointing at the blank. The
operator identified the site as CNCEC on 2026-08-09, so it is written here
explicitly, guarded on the value still being blank.

Nothing else is guessed. Any OTHER site-less employee is left alone and
reported by the /employees/data-quality endpoint, the same discipline as the
Consumption-Log row that has a Location and no serial
(EXCEL_LOCATION_SYNC_RUNLOG.md).
"""
from alembic import op
import sqlalchemy as sa

revision = "d2f84b19e57c"
down_revision = "c9e35a71d4b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_movements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("employee_id_number", sa.Text(), nullable=False),
        sa.Column("from_site", sa.Text(), nullable=True),
        sa.Column("to_site", sa.Text(), nullable=False),
        sa.Column("effective_date", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("moved_by", sa.Text(), nullable=False),
        # applied | reverted. HOD transfers take effect immediately (ruling
        # R4) — only the QC *user* transfer needs an admin's second signature,
        # because that one rewrites an authentication row.
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'applied'")),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    for col in (
        sa.Column("Designation", sa.Text(), nullable=True),
        sa.Column("Worker_Type", sa.Text(), nullable=True),
        sa.Column("Company", sa.Text(), nullable=True),
    ):
        op.add_column("employees", col)

    data_upgrade(op.get_bind())


def data_upgrade(conn) -> None:
    """DATA step — see cutover_migrate.run_data_migrations.

    The one authorised backfill (operator, 2026-08-09). Guarded on the value
    still being blank so a re-run, or a later correction by a human, is never
    overwritten — which is also what makes it safe to run twice.
    """
    conn.execute(sa.text("""
        UPDATE employees
           SET "Site_ID" = 'CNCEC'
         WHERE "ID_Number" = '30816'
           AND COALESCE("Site_ID", '') = ''
    """))


def downgrade() -> None:
    # The backfill is NOT reversed. Blanking it again would restore a row that
    # is invisible to every supervisor request, and a schema downgrade is not
    # a reason to reintroduce a data defect.
    for name in ("Company", "Worker_Type", "Designation"):
        op.drop_column("employees", name)
    op.drop_table("employee_movements")
