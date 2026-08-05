"""asset tracking — asset_units + asset_movements (serials + GPS)

Revision ID: e9f2a4c68b71
Revises: d5b83c17e604
Create Date: 2026-08-04

THE "TWO HAMMERS" PROBLEM. Two hammers share one SAP code, so scanning either
label resolves to the same inventory row and the system cannot say which one
you are holding — or where the other one is.

The workbook cannot solve this, and the analysis says so plainly:

  · `Consumption Log.Serial No.` is populated on 101 of 1,110 rows and the
    values are BATCH/LOT numbers, not asset serials — `3441` appears on BOTH
    components of one CUMIFLOOR primer, `100160374` on garnet, and consumables
    do not have unique serials at all.
  · `Receipt Log.Serial No.` has 79 real values out of 565 — closer, but
    nowhere near coverage.
  · Every `Location` column is blank, single-valued, or a site name.

So the app owns asset identity, and `asset_units` is one row per PHYSICAL
THING.

IDENTITY IS `(Site_ID, SAP_Code, serial_no)`, deliberately mirroring rule 1's
lesson: the thing that distinguishes two physical objects belongs IN THE KEY.
Pooling by SAP is what makes three hammers indistinguishable, exactly as
pooling by Material_Code made four unlike drums indistinguishable.

ASSETS ONLY. A row exists only where an operator creates one. Consumables never
get rows — the brief's "consumables won't have locations" is enforced by
absence rather than by a flag nobody sets.

`asset_movements` is APPEND-ONLY, the same discipline as `system_audit_log`
(never deleted): "where has this hammer been" is a query, not a guess.
`asset_units.current_*` is a denormalised cache of the newest movement, written
in the same transaction.

⚠️ GPS IS PERSONAL DATA. `lat`/`lng` on a movement row is where an EMPLOYEE was
standing when they scanned. It is best-effort by design — a denied browser
permission still records the move, with the coordinates NULL — and it is
readable only by roles that can already see the asset. This is the first
genuinely personal data the system stores, and it is called out in the manual
for that reason.
"""
from alembic import op
import sqlalchemy as sa

revision = "e9f2a4c68b71"
down_revision = "d5b83c17e604"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_units",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Site_ID", sa.Text(), nullable=False),
        sa.Column("SAP_Code", sa.Text(), nullable=False),
        # The operator's own unique serial. NOT the workbook's `Serial No.`,
        # which is a batch number — see the module docstring.
        sa.Column("serial_no", sa.Text(), nullable=False),
        # QR payload when the sticker carries something other than the serial.
        sa.Column("asset_tag", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'in_stock'")),
        sa.Column("current_location_id", sa.Integer(), nullable=True),
        sa.Column("current_lat", sa.Float(), nullable=True),
        sa.Column("current_lng", sa.Float(), nullable=True),
        sa.Column("gps_accuracy_m", sa.Float(), nullable=True),
        # Free text for the places a rack code cannot express ("with the
        # subcontractor", "in the pickup"). A location does not have to be a
        # shelf, and forcing one would make people leave it blank instead.
        sa.Column("location_note", sa.Text(), nullable=True),
        sa.Column("holder", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_by", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Site_ID", "SAP_Code", "serial_no",
                            name="uq_asset_units_site_sap_serial"),
    )
    # The scan path: "this SAP — which units exist?" and "this serial — which
    # unit is it?". Both are the access path, not speculative.
    op.create_index("ix_asset_units_sap_site", "asset_units",
                    ["SAP_Code", "Site_ID"], unique=False)
    op.create_index("ix_asset_units_serial", "asset_units",
                    ["serial_no"], unique=False)

    op.create_table(
        "asset_movements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("asset_unit_id", sa.Integer(), nullable=False),
        sa.Column("moved_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("moved_by", sa.Text(), nullable=True),
        sa.Column("from_location_id", sa.Integer(), nullable=True),
        sa.Column("to_location_id", sa.Integer(), nullable=True),
        sa.Column("from_note", sa.Text(), nullable=True),
        sa.Column("to_note", sa.Text(), nullable=True),
        # NULL whenever the browser denied or could not fix a position. GPS is
        # best-effort: it must never block recording where something went.
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),   # qr_scan | manual | issue | return
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_movements_unit", "asset_movements",
                    ["asset_unit_id", "moved_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_asset_movements_unit", table_name="asset_movements")
    op.drop_table("asset_movements")
    op.drop_index("ix_asset_units_serial", table_name="asset_units")
    op.drop_index("ix_asset_units_sap_site", table_name="asset_units")
    op.drop_table("asset_units")
