"""warehouse rack locator — storage_locations + material_locations

Revision ID: d5b83c17e604
Revises: c1a72e5b83d9
Create Date: 2026-08-04

"Which rack is this in?" currently has no answer anywhere in the system. The
2026-08-04 workbook added a `Current Location` column to the Inventory sheet
but it is BLANK in 452 of 452 rows, so the spreadsheet cannot seed this — the
app has to own it.

TWO TABLES, NOT A COLUMN ON `inventory`.

  `storage_locations`   the physical places: zone / rack / row / bin, each with
                        a `code` that is also the QR payload on the shelf label.
  `material_locations`  which SAP lives where. A material may legitimately sit
                        in more than one rack, so this is many-to-many with an
                        `is_primary` flag marking the one to walk to first.

A `Location` column on `inventory` was rejected: `inventory` already carries a
UNIQUE on `Material_Code` (a pre-existing constraint that stops the variant
SAPs of a multi-part system from all holding their code), it is one row per
SAP, and it is therefore the wrong grain for a material in three places.

ON THE INDEXES (rule 11 — benchmarked, not added on principle). Both are on
the ACCESS PATH rather than speculative:

  · `ix_material_locations_sap` serves the whole point of the feature — the
    store keeper's lookup, `WHERE "SAP_Code" = … AND "Site_ID" = …`.
  · the two UNIQUE constraints are the data model itself: one row per code per
    site, one row per (site, SAP, location).

No benchmark table is quoted here because there is nothing yet to benchmark —
these tables start empty. The honest measurement is recorded in the run log
against real data once the operator has entered racks; at the scale in view
(452 inventory rows, a few hundred locations) an indexed lookup is a
single-page read, and no cache or search engine is warranted.
"""
from alembic import op
import sqlalchemy as sa

revision = "d5b83c17e604"
down_revision = "c1a72e5b83d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_locations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Site_ID", sa.Text(), nullable=False),
        # The QR payload printed on the shelf label, e.g. "A-03-2".
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("zone", sa.Text(), nullable=True),
        sa.Column("rack_no", sa.Text(), nullable=True),
        sa.Column("row_no", sa.Text(), nullable=True),
        sa.Column("bin_no", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'active'")),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Site_ID", "code", name="uq_storage_locations_site_code"),
    )

    op.create_table(
        "material_locations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Site_ID", sa.Text(), nullable=False),
        sa.Column("SAP_Code", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("Site_ID", "SAP_Code", "location_id",
                            name="uq_material_locations_site_sap_loc"),
    )
    op.create_index("ix_material_locations_sap", "material_locations",
                    ["SAP_Code", "Site_ID"], unique=False)
    op.create_index("ix_storage_locations_site", "storage_locations",
                    ["Site_ID", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_storage_locations_site", table_name="storage_locations")
    op.drop_index("ix_material_locations_sap", table_name="material_locations")
    op.drop_table("material_locations")
    op.drop_table("storage_locations")
