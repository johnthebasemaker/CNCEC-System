"""the Head of Qualities — escalations and stagnation rules

TWO TABLES FOR A ROLE THAT WRITES ALMOST NOTHING.

`qc_escalations` is the only thing a `qc_hod` may create, and every row is a
MESSAGE — never a change to stock, an inspection decision or a document. It is
a LOG rather than a fire-and-forget notification because uncertified Surface
Shield is a standing condition, not an event: the second and third chase are
the ones that matter, and they only exist if the first was written down.

`qc_stagnation_rules` holds the thresholds, seeded at 90 days without movement
and 60 days to expiry (operator ruling Q9). A table and not a constant for the
same reason `mh_ot_threshold_*` is: 90 days is the operator's policy, and
changing a policy must not be a release.

⚠️ NO CHANGE TO `users`. `qc_hod` is a value in `users.role`, and the role
carries no site — exactly like `logistics` and `auditor`. Cross-site reach
comes from the named exemption in `auth.QC_OVERSIGHT_ROLES`, not from a column.

Revision ID: c7e1a4b92d63
Revises: a9f2c6b40d18
Create Date: 2026-08-23 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7e1a4b92d63"
down_revision: Union[str, None] = "a9f2c6b40d18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The controlled category, and the numbers behind it. `Surface Shields` is what
# `quality.controlled_category()` resolves to on every live box; seeding the
# rule against it means the dashboard has a threshold on day one instead of an
# empty settings form and a silent zero.
_SEED = (("Surface Shields", 90, 60),)


def upgrade() -> None:
    op.create_table(
        "qc_escalations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("raised_by", sa.Text(), nullable=False),
        sa.Column("target_role", sa.Text(), nullable=False),
        sa.Column("target_site", sa.Text(), nullable=True),
        sa.Column("target_warehouse", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("SAP_Code", sa.Text(), nullable=True),
        sa.Column("Material_Code", sa.Text(), nullable=True),
        sa.Column("Lot_Number", sa.Text(), nullable=True),
        sa.Column("PO_Number", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'open'")),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("notification_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qc_escalations_open", "qc_escalations",
                    ["status", "kind", "created_at"])

    op.create_table(
        "qc_stagnation_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Category", sa.Text(), nullable=False),
        sa.Column("stagnant_days", sa.Integer(), nullable=False,
                  server_default=sa.text("90")),
        sa.Column("expiry_warn_days", sa.Integer(), nullable=False,
                  server_default=sa.text("60")),
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'active'")),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Category"),
    )

    data_upgrade(op.get_bind())


def data_upgrade(conn) -> None:
    """DATA step — see cutover_migrate.run_data_migrations.

    Idempotent: ON CONFLICT DO NOTHING, so re-running changes nothing and an
    operator's edited threshold is never reset to the seed.
    """
    for category, stagnant, expiry in _SEED:
        conn.execute(sa.text(
            'INSERT INTO qc_stagnation_rules ("Category", stagnant_days, '
            "expiry_warn_days, updated_by) "
            "VALUES (:c, :s, :e, 'alembic') "
            'ON CONFLICT ("Category") DO NOTHING'),
            {"c": category, "s": stagnant, "e": expiry})
    n = conn.execute(sa.text(
        "SELECT COUNT(*) FROM qc_stagnation_rules")).scalar() or 0
    print(f"  · qc_stagnation_rules holds {n} rule(s)")


def downgrade() -> None:
    op.drop_table("qc_stagnation_rules")
    op.drop_index("ix_qc_escalations_open", table_name="qc_escalations")
    op.drop_table("qc_escalations")
