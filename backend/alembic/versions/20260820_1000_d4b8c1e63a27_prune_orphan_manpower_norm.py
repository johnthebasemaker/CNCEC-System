"""prune the orphaned blasting benchmark — Phase 8 slice 8a

A RENAME IN THE WORKBOOK IS AN INSERT, NOT AN UPDATE.

`sme_manpower_norm`'s identity is five parts and `Activity` is one of them, so
when the operator renamed

    'Blasting Civil PU Area'  →  'Blasting Civil PU 4mm Area'

the importer created the new row and left the old one standing. The sync
reported "+2 ~0 rejected=0" and every workbook row matched, both truthfully:
there is no pass for rows that VANISH from the workbook, so nothing looked at
the leftover.

It is not harmless. `services/planner.py` treats every system-agnostic norm as
part of surface prep, so the orphan added a phantom 0.825 man-hours per m² to
every prep plan — one of the five blasting benchmarks that were being summed
where exactly one should apply.

Deleting it is safe by construction: `sme_manpower_norm_role` cascades, and
`sme_execution_entry.Norm_ID` is ON DELETE SET NULL with the benchmark itself
snapshotted into the `Bench_*` columns at submission. An entry that used this
row keeps every number it was judged against and loses only the pointer.

⚠️ THE ORPHAN IS NAMED, NOT INFERRED. This deletes one identity that is known
to be a leftover. It does not delete "norms missing from the workbook" in
general — a migration cannot see the workbook, and a rule that guessed would
be the same class of mistake as the one it is cleaning up. The general case is
the `orphans` report that `bulk_import.plan_sme_manpower_norms` now returns and
`tools/pg_excel_sync.py` prints.

Revision ID: d4b8c1e63a27
Revises: e5b2d7c94a16
Create Date: 2026-08-20 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4b8c1e63a27"
down_revision: Union[str, None] = "e5b2d7c94a16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The five-part identity of the leftover, spelled out so the row this migration
# touches is readable without running it.
_ORPHAN = {
    "Type": "CV",
    "Lining_System_Code": "ESC1",
    "Execution_Sub_Activity_Code": "ESC1",
    "Activity": "Blasting Civil PU Area",
    "Variant_Key": "",
}

_WHERE = (
    'WHERE "Type" = :Type AND "Lining_System_Code" = :Lining_System_Code '
    'AND "Execution_Sub_Activity_Code" = :Execution_Sub_Activity_Code '
    'AND "Activity" = :Activity '
    'AND COALESCE("Variant_Key", \'\') = :Variant_Key'
)


def upgrade() -> None:
    data_upgrade(op.get_bind())


def data_upgrade(conn) -> None:
    """DATA step — see cutover_migrate.run_data_migrations.

    Idempotent: once the row is gone the DELETE matches nothing and the whole
    function is a no-op. Safe to re-run, and safe on a database that never had
    the orphan (a fresh install imports the corrected workbook directly).
    """
    row = conn.execute(sa.text(
        f'SELECT id FROM sme_manpower_norm {_WHERE}'), _ORPHAN).fetchone()
    if row is None:
        print("  · no orphaned 'Blasting Civil PU Area' benchmark — nothing to do")
        return

    # Report before removing. An execution entry that was judged against this
    # benchmark keeps its snapshot, but somebody should know the pointer went.
    refs = conn.execute(sa.text(
        'SELECT COUNT(*) FROM sme_execution_entry WHERE "Norm_ID" = :i'),
        {"i": row[0]}).scalar() or 0
    if refs:
        print(f"  ⚠ {refs} execution entr(ies) reference benchmark id {row[0]}. "
              f"Their Bench_* snapshot columns are unaffected; Norm_ID becomes "
              f"NULL.")

    conn.execute(sa.text(f'DELETE FROM sme_manpower_norm {_WHERE}'), _ORPHAN)
    print(f"  · pruned orphaned benchmark id {row[0]} "
          f"('Blasting Civil PU Area') — superseded by "
          f"'Blasting Civil PU 4mm Area'")


def downgrade() -> None:
    """Deliberately not restored.

    The row is a duplicate of 'Blasting Civil PU 4mm Area' under a superseded
    name. Re-creating it would re-introduce the double-count this migration
    exists to remove, and there is no state in which having it back is correct.
    """
