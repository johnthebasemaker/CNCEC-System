"""QSEP polish — an asset is ONE thing globally, and it moves by approval

Revision ID: a3c17e9b25d4
Revises: e6a91c37b208
Create Date: 2026-08-11

THE KEY WAS TOO WIDE, AND THAT IS WHAT LET A THING EXIST TWICE

`asset_units` was UNIQUE on (Site_ID, SAP_Code, serial_no). Site in the key
means hammer #A-1042 can exist at CNCEC **and** at another site
simultaneously — two rows, two custody chains, two GPS fixes, for one
physical hammer. The constraint says "unique per site", but a serial number
is not a per-site fact; it is stamped on the object.

The key is now (SAP_Code, serial_no), globally. `Site_ID` stays on the row —
it is WHERE THE THING IS, which is data, not identity. That distinction is
the same lesson as rule 1: what distinguishes two physical objects belongs
in the key, and what merely describes one does not.

`(Site_ID, SAP_Code, serial_no)` is kept as a plain INDEX. It was the unique
constraint and is also the shape of the by-site lookups, so dropping it
outright would trade a correctness fix for a scan.

⚠️ THE MIGRATION REFUSES TO RUN IF DUPLICATES EXIST. Tightening a key over
data that violates it is how a migration half-applies in production. It is
zero rows today (`asset_units` is empty), but the guard is what makes that
verifiable rather than assumed — and it names the offending serials so an
operator can merge them by hand. Merging cannot be automated: two rows for
one hammer have two custody histories, and only a human knows which is real.

TRANSFERS ARE APPROVED BY THE SITE GIVING THE ASSET AWAY

Silently updating `Site_ID` is how a tool leaves a yard without anybody
agreeing to it. `asset_transfers` holds the request until the SOURCE site's
HOD approves — the site losing the asset is the one with something at stake
and the one that can confirm it physically left. The receiving site finding
out afterwards is the failure this prevents.
"""
from alembic import op
import sqlalchemy as sa

revision = "a3c17e9b25d4"
down_revision = "e6a91c37b208"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    dupes = conn.execute(sa.text('''
        SELECT "SAP_Code", serial_no, COUNT(*) AS n,
               STRING_AGG(DISTINCT COALESCE("Site_ID",'?'), ', ') AS sites
          FROM asset_units
         GROUP BY "SAP_Code", serial_no
        HAVING COUNT(*) > 1
         ORDER BY n DESC LIMIT 20
    ''')).fetchall()
    if dupes:
        detail = "; ".join(f"{r[0]}/{r[1]} × {r[2]} at {r[3]}" for r in dupes)
        raise RuntimeError(
            "asset_units already holds the same (SAP_Code, serial_no) at more "
            "than one site, so it cannot be made globally unique without "
            "losing custody history. Merge these by hand first — only a human "
            f"knows which row is the real one:\n  {detail}")

    op.drop_constraint("uq_asset_units_site_sap_serial", "asset_units",
                       type_="unique")
    # Kept as a plain index: it was the unique key and is also the shape of
    # every by-site asset lookup, so dropping it would trade a correctness
    # fix for a sequential scan.
    op.create_index("ix_asset_units_site_sap_serial", "asset_units",
                    ["Site_ID", "SAP_Code", "serial_no"])
    op.create_unique_constraint("uq_asset_units_sap_serial", "asset_units",
                                ["SAP_Code", "serial_no"])

    op.create_table(
        "asset_transfers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("asset_unit_id", sa.Integer(), nullable=False),
        sa.Column("SAP_Code", sa.Text(), nullable=False),
        sa.Column("serial_no", sa.Text(), nullable=False),
        sa.Column("from_site", sa.Text(), nullable=False),
        sa.Column("to_site", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        # pending_source_hod | approved | rejected | cancelled
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'pending_source_hod'")),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("movement_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # One open request per asset. Without it two sites can both have a
    # pending claim on the same hammer and whichever HOD clicks second
    # silently overwrites the first — a partial unique index says so in the
    # database instead of in a comment.
    op.execute('CREATE UNIQUE INDEX ux_asset_transfer_open '
               "ON asset_transfers (asset_unit_id) "
               "WHERE status = 'pending_source_hod'")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_asset_transfer_open")
    op.drop_table("asset_transfers")
    op.drop_constraint("uq_asset_units_sap_serial", "asset_units", type_="unique")
    op.drop_index("ix_asset_units_site_sap_serial", table_name="asset_units")
    op.create_unique_constraint("uq_asset_units_site_sap_serial", "asset_units",
                                ["Site_ID", "SAP_Code", "serial_no"])
