"""sme_recipe_execution_sub_activity — ESC joins the recipe line identity

2026-08 workbook overhaul: For_1_SQM.xlsx gained an
`Execution_Sub_Activity_Code` column (ESC11, ESC21, ESC22 …) naming which
execution sub-activity each benchmark quantity belongs to.

⚠️ THIS SPLITS A NUMBER THAT WAS PREVIOUSLY MERGED. Recipe line identity was
(Lining_System_Code, Material_Code, SAP_Code). Under the new workbook that key
is violated twice — LSC2/GI-6002243/1049 and LSC2/GI-6002244/1050 each appear
under BOTH ESC21 (primer) and ESC22 (screed):

    LSC2 · GI-6002243 · 1049  →  ESC21 0.2700  +  ESC22 1.4674   (merged 1.7374)
    LSC2 · GI-6002244 · 1050  →  ESC21 0.1350  +  ESC22 0.7326   (merged 0.8676)

`bulk_import.plan_sme_recipes` did not reject that collision, it SUMMED it as a
deliberate "coat merge". That was correct while a lining system was consumed as
a whole. It is wrong the moment a supervisor reports actuals against ONE
sub-activity: a correct primer draw measured against 1.7374 instead of 0.2700
reads as 15.5 % of benchmark — an apparent 84.5 % under-consumption that would
demand a written justification for a variance that does not exist.

WHY THERE IS NO DATA BACKFILL HERE. The ESC values exist ONLY in
For_1_SQM.xlsx, and `*.xlsx` is gitignored — a production box does not have the
workbook, so a migration that read it would behave differently on every host.
Migrations stay deterministic. Existing rows therefore get `''`, meaning "not
yet classified by a workbook sync", and `plan_sme_recipes` ADOPTS an
unclassified row on the next sync rather than inserting a duplicate beside it.
Run `tools/pg_excel_sync.py --site <SITE> --commit` after upgrading.

`''` rather than NULL is load-bearing: Postgres treats NULLs as distinct, so a
nullable column in a unique constraint stops the constraint constraining.

Revision ID: f1d3b7a24c60
Revises: c7a93e5d2b18
Create Date: 2026-08-18 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1d3b7a24c60"
down_revision: Union[str, None] = "c7a93e5d2b18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_UQ = "sme_recipe_code_mat_sap_key"
_NEW_UQ = "sme_recipe_code_esc_mat_sap_key"


def upgrade() -> None:
    # 1. nullable first, so the column can be added to a populated table
    op.add_column("sme_recipe",
                  sa.Column("Execution_Sub_Activity_Code", sa.Text(),
                            nullable=True, server_default=sa.text("''")))
    # 2. every pre-existing row is 'unclassified' until a sync names it
    op.execute('UPDATE sme_recipe SET "Execution_Sub_Activity_Code" = \'\' '
               'WHERE "Execution_Sub_Activity_Code" IS NULL')
    # 3. assert the invariant BEFORE relying on it
    op.execute("""
        DO $$
        DECLARE n bigint;
        BEGIN
            SELECT count(*) INTO n FROM sme_recipe
             WHERE "Execution_Sub_Activity_Code" IS NULL;
            IF n > 0 THEN
                RAISE EXCEPTION
                  'sme_recipe still has % row(s) with a NULL '
                  'Execution_Sub_Activity_Code', n;
            END IF;
        END $$;
    """)
    # 4. now it can be NOT NULL
    op.alter_column("sme_recipe", "Execution_Sub_Activity_Code", nullable=False)
    # 5. widen the identity. Safe in this order: adding a constant column to a
    #    key that was already unique cannot create a duplicate.
    op.drop_constraint(_OLD_UQ, "sme_recipe", type_="unique")
    op.create_unique_constraint(
        _NEW_UQ, "sme_recipe",
        ["Lining_System_Code", "Execution_Sub_Activity_Code",
         "Material_Code", "SAP_Code"])


def data_upgrade(conn) -> None:
    """DATA step — see cutover_migrate.run_data_migrations. DELIBERATELY EMPTY.

    The `UPDATE … WHERE IS NULL` in upgrade() exists only to let an EXISTING
    populated table reach NOT NULL. A cut-over box builds sme_recipe from
    models.py, where the column is already NOT NULL DEFAULT '', so there is
    nothing to correct.

    It is declared rather than omitted because the cutover guard requires every
    migration carrying DML to state its data step — including "none, because".
    """
    return


def downgrade() -> None:
    # Narrowing the key can genuinely collide — two ESC rows for one
    # (code, material, SAP) is exactly what this migration exists to allow. Say
    # so instead of failing inside the constraint with a duplicate-key error.
    op.execute("""
        DO $$
        DECLARE n bigint;
        BEGIN
            SELECT count(*) INTO n FROM (
                SELECT 1 FROM sme_recipe
                 GROUP BY "Lining_System_Code", "Material_Code", "SAP_Code"
                HAVING count(*) > 1) d;
            IF n > 0 THEN
                RAISE EXCEPTION
                  'cannot downgrade: % (code, material, SAP) group(s) hold more '
                  'than one sub-activity row. Merge or delete them first.', n;
            END IF;
        END $$;
    """)
    op.drop_constraint(_NEW_UQ, "sme_recipe", type_="unique")
    op.create_unique_constraint(
        _OLD_UQ, "sme_recipe",
        ["Lining_System_Code", "Material_Code", "SAP_Code"])
    op.drop_column("sme_recipe", "Execution_Sub_Activity_Code")
