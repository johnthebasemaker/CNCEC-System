"""sme_component_pooling — sme_inventory_seed keyed per PHYSICAL component

2026-07-30 COMPONENT IDENTITY ruling, overturning the 2026-07-18 Material_Code
pooling rule. b3f2a9c47d18 added SAP_Code to this table but left the primary key
on Material_Code alone, so the four Comp-A/B/C/D drums of a multi-part system
(GI-8005765 at SAPs 1041 / 1041-1 / 1041-2 / 1041-3) collapsed into ONE stock
row: quantities summed, SAP codes joined into a comma list, and the first
component's name standing in for all four. Every bottleneck ratio computed off
that row was meaningless — the earlier recipe lines drained stock belonging to
the later ones.

The key widens to (Material_Code, SAP_Code).

⚠️ THE SUMMED QUANTITIES ARE NOT RECOVERABLE FROM THE POOLED ROW. This migration
makes the SCHEMA correct and leaves the surviving row pointing at the FIRST SAP
of its old comma list, carrying the old pooled figure. That row is valid but
stale until the workbook is reloaded:

    .venv/bin/python tools/pg_excel_sync.py --site CNCEC --commit

The reload converges exactly: the surviving (code, first-SAP) row is UPDATED
with that component's own quantities and the remaining components are INSERTED.
No row is orphaned, because every SAP in a comma list is a real component SAP.

Revision ID: a4e9b1c73f28
Revises: f1a7c9e83b52
Create Date: 2026-07-30 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4e9b1c73f28'
down_revision: Union[str, None] = 'f1a7c9e83b52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# First SAP of a comma list, whitespace stripped ("1041, 1041-1" -> "1041").
# The ERP also writes single variants as "1043 - 2", hence the space strip.
_FIRST_SAP = (
    "REPLACE(BTRIM(SPLIT_PART(COALESCE(\"SAP_Code\", ''), ',', 1)), ' ', '')")


def upgrade() -> None:
    # 1-2. normalise the SAP codes and prove the widened key is unique
    data_upgrade(op.get_bind())
    # 3. widen the primary key
    op.alter_column('sme_inventory_seed', 'SAP_Code',
                    existing_type=sa.Text(), nullable=False,
                    server_default=sa.text("''"))
    op.drop_constraint('sme_inventory_seed_pkey', 'sme_inventory_seed',
                       type_='primary')
    op.create_primary_key('sme_inventory_seed_pkey', 'sme_inventory_seed',
                          ['Material_Code', 'SAP_Code'])


def data_upgrade(conn) -> None:
    """DATA step — see cutover_migrate.run_data_migrations.

    The legacy SQLite database stores a component's SAP as a comma list
    ("1041, 1041-1"), so a freshly cut-over box arrives holding exactly the
    shape this normalises. Idempotent: SPLIT_PART of an already-collapsed
    value is itself.
    """
    # 1. collapse the comma lists down to the one component the row survives as
    conn.execute(sa.text(f'UPDATE sme_inventory_seed SET "SAP_Code" = {_FIRST_SAP}'))
    conn.execute(sa.text('UPDATE sme_inventory_seed SET "SAP_Code" = \'\' '
                         'WHERE "SAP_Code" IS NULL'))
    # 2. guard: the widened key must be unique BEFORE it becomes the PK, so a
    #    surprise collision fails the migration instead of the constraint
    dup = conn.execute(sa.text(
        'SELECT "Material_Code", "SAP_Code", COUNT(*) c FROM sme_inventory_seed '
        'GROUP BY 1, 2 HAVING COUNT(*) > 1')).fetchall()
    if dup:
        raise RuntimeError(
            "sme_inventory_seed has duplicate (Material_Code, SAP_Code) rows "
            f"after SAP normalization: {[tuple(r) for r in dup][:5]}. Resolve "
            "them by hand — silently merging stock figures is not safe.")


def downgrade() -> None:
    # Narrowing the key means re-pooling: sum the components back onto the row
    # with the lowest SAP and drop the rest. Lossy by nature — the per-component
    # split cannot be reconstructed afterwards without another workbook reload.
    op.execute('''
        WITH agg AS (
            SELECT "Material_Code",
                   MIN("SAP_Code")             AS keep_sap,
                   SUM("Initial_Available_Qty") AS avail,
                   SUM("Initial_Ordered_Qty")   AS ordered,
                   STRING_AGG(DISTINCT "SAP_Code", ', ' ORDER BY "SAP_Code") AS saps
            FROM sme_inventory_seed GROUP BY "Material_Code"
        )
        UPDATE sme_inventory_seed s
           SET "Initial_Available_Qty" = a.avail,
               "Initial_Ordered_Qty"   = a.ordered,
               "SAP_Code"              = a.saps
          FROM agg a
         WHERE s."Material_Code" = a."Material_Code"
           AND s."SAP_Code" = a.keep_sap
    ''')
    op.execute('''
        DELETE FROM sme_inventory_seed s
         WHERE EXISTS (SELECT 1 FROM sme_inventory_seed o
                        WHERE o."Material_Code" = s."Material_Code"
                          AND o."SAP_Code" LIKE '%,%')
           AND s."SAP_Code" NOT LIKE '%,%'
    ''')
    op.drop_constraint('sme_inventory_seed_pkey', 'sme_inventory_seed',
                       type_='primary')
    op.create_primary_key('sme_inventory_seed_pkey', 'sme_inventory_seed',
                          ['Material_Code'])
    op.alter_column('sme_inventory_seed', 'SAP_Code',
                    existing_type=sa.Text(), nullable=True,
                    server_default=None)
