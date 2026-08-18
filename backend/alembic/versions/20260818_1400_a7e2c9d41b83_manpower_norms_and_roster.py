"""manpower_norms_and_roster — Phase 3 + 4

Phase 3 — the manpower benchmark master, from Manpower_Hour_Details.xlsx:
  · mh_roles                — the role/designation master (dropdown + HOD adds)
  · sme_manpower_norm       — one productivity benchmark per activity
  · sme_manpower_norm_role  — that benchmark's crew composition, per role

Phase 4 — the roster:
  · mh_employees.Shift        Day | Night
  · mh_employees.Worker_Type  OWN | Supply  →  GI | NON_GI

⚠️ THE WORKER_TYPE RENAME COVERS TWO VALUES, NOT ONE. The operator's ruling
named OWN→GI and asked for NON_GI to be added. The column's existing vocabulary
was OWN | Supply (`manhours._COMPANY_DEFAULTS`), and `Supply` IS the non-GI
case — supplied labour, defaulting to company DMC. Migrating only OWN would
leave `Supply` as a third value that every new threshold rule silently misses,
so both are mapped. Live data holds 22 OWN rows and no Supply rows today; the
IMPORT PATH still produces 'Supply', which is why the mapping cannot be
skipped as "no rows affected".

Anything else — a value neither vocabulary knows — is left ALONE and reported
by the guard below rather than being forced to a default. A worker silently
reclassified is a worker paid against the wrong overtime threshold.

Revision ID: a7e2c9d41b83
Revises: f1d3b7a24c60
Create Date: 2026-08-18 14:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7e2c9d41b83"
down_revision: Union[str, None] = "f1d3b7a24c60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WORKER_TYPE_MAP = {"OWN": "GI", "Supply": "NON_GI"}

# The nine role columns of Manpower_Hour_Details.xlsx, in sheet order. Seeded
# here so the dropdown is populated before the first workbook import, and so a
# site that never imports still has a usable roster.
_WORKBOOK_ROLES = [
    ("BLASTER", "Blaster"), ("POTMAN", "Potman"),
    ("RUBBER_LINER", "Rubber Liner"), ("COATING_APPLICATOR", "Coating applicator"),
    ("SHEET_PREPARATOR", "Sheet Preparator"), ("MASON", "Mason"),
    ("MORTAR_MIXER", "mortar mixer"), ("BRICK_CUTTER", "brick cutter"),
    ("HELPER", "Helper"),
]


def upgrade() -> None:
    # ── Phase 3: the benchmark master ───────────────────────────────────────
    op.create_table(
        "mh_roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Role_Code", sa.Text(), nullable=False),
        sa.Column("Name", sa.Text(), nullable=False),
        sa.Column("Source", sa.Text(), server_default=sa.text("'custom'"),
                  nullable=False),
        sa.Column("Sort_Order", sa.Integer(), server_default=sa.text("0"),
                  nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"),
                  nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Role_Code"),
    )
    op.create_table(
        "sme_manpower_norm",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Activity_Code", sa.Text(), nullable=True),
        sa.Column("Type", sa.Text(), nullable=False),
        sa.Column("System", sa.Text(), nullable=True),
        sa.Column("Lining_System_Code", sa.Text(), nullable=False),
        sa.Column("Execution_Sub_Activity_Code", sa.Text(), nullable=False),
        sa.Column("Activity", sa.Text(), nullable=False),
        sa.Column("Sub_Activity", sa.Text(), nullable=True),
        sa.Column("Variant_Key", sa.Text(), server_default=sa.text("''"),
                  nullable=False),
        sa.Column("Crew_Size", sa.Float(), server_default=sa.text("0"),
                  nullable=False),
        sa.Column("Hours_Per_Shift", sa.Float(), server_default=sa.text("0"),
                  nullable=False),
        sa.Column("Manhours_Per_Shift", sa.Float(), server_default=sa.text("0"),
                  nullable=False),
        sa.Column("Standard_Productivity_Per_Shift", sa.Float(),
                  server_default=sa.text("0"), nullable=False),
        sa.Column("SQM_Per_Hour_Per_Person", sa.Float(),
                  server_default=sa.text("0"), nullable=False),
        sa.Column("Remarks", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Type", "Lining_System_Code",
                            "Execution_Sub_Activity_Code", "Activity",
                            "Variant_Key",
                            name="sme_manpower_norm_identity_key"),
    )
    op.create_table(
        "sme_manpower_norm_role",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Norm_ID", sa.Integer(), nullable=False),
        sa.Column("Role_Code", sa.Text(), nullable=False),
        sa.Column("Headcount", sa.Float(), server_default=sa.text("0"),
                  nullable=False),
        sa.ForeignKeyConstraint(["Norm_ID"], ["sme_manpower_norm.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Norm_ID", "Role_Code"),
    )

    # ── Phase 4: the roster ─────────────────────────────────────────────────
    op.add_column("mh_employees",
                  sa.Column("Shift", sa.Text(), server_default=sa.text("'Day'"),
                            nullable=True))
    op.execute("""UPDATE mh_employees SET "Shift" = 'Day' WHERE "Shift" IS NULL""")
    op.alter_column("mh_employees", "Shift", nullable=False)

    data_upgrade(op.get_bind())

    op.alter_column("mh_employees", "Worker_Type",
                    server_default=sa.text("'GI'"))


def data_upgrade(conn) -> None:
    """DATA step — see cutover_migrate.run_data_migrations.

    Idempotent: the role seed is ON CONFLICT DO NOTHING, and re-running the
    worker-type map is a no-op once no OWN/Supply rows remain.
    """
    for i, (code, name) in enumerate(_WORKBOOK_ROLES):
        conn.execute(sa.text(
            'INSERT INTO mh_roles ("Role_Code", "Name", "Source", "Sort_Order", '
            'created_by) VALUES (:c, :n, \'workbook\', :o, \'alembic\') '
            'ON CONFLICT ("Role_Code") DO NOTHING'),
            {"c": code, "n": name, "o": i})

    for old, new in _WORKER_TYPE_MAP.items():
        conn.execute(sa.text(
            'UPDATE mh_employees SET "Worker_Type" = :new '
            'WHERE "Worker_Type" = :old'), {"old": old, "new": new})

    # The HOD-configurable overtime thresholds. Seeded as ROWS, not left to the
    # code default, so the settings page shows a real value on a fresh box and
    # an HOD can see what the system is applying before they change it.
    for key, value in (("mh_ot_threshold_gi", "8"),
                       ("mh_ot_threshold_non_gi", "10")):
        conn.execute(sa.text(
            "INSERT INTO app_settings (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO NOTHING"), {"k": key, "v": value})

    # Report, never coerce. A value we do not recognise is a data question for
    # a human — defaulting it would decide somebody's overtime threshold by
    # accident and leave no trace that a decision was made.
    stray = conn.execute(sa.text(
        'SELECT DISTINCT "Worker_Type" FROM mh_employees '
        "WHERE \"Worker_Type\" NOT IN ('GI', 'NON_GI')")).fetchall()
    if stray:
        print(f"  ⚠ mh_employees holds unrecognised Worker_Type value(s): "
              f"{[r[0] for r in stray]} — left untouched, classify them by hand")


def downgrade() -> None:
    op.alter_column("mh_employees", "Worker_Type",
                    server_default=sa.text("'OWN'"))
    for old, new in _WORKER_TYPE_MAP.items():
        op.execute(sa.text(
            'UPDATE mh_employees SET "Worker_Type" = :old '
            'WHERE "Worker_Type" = :new').bindparams(old=old, new=new))
    op.drop_column("mh_employees", "Shift")
    op.drop_table("sme_manpower_norm_role")
    op.drop_table("sme_manpower_norm")
    op.drop_table("mh_roles")
