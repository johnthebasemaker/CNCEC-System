"""QSEP slice 1-2 — the QC role's inspection ledger, transfers, and MTC reach

Revision ID: b4d17c8e93a2
Revises: a71e93b4c2f8
Create Date: 2026-08-09

Three things, one migration, because they only make sense together.

**qc_inspections** is the quality decision ledger. It is keyed to the LOT
rather than to the receipt, because a lot is what physically sits on a shelf
and what `lots` already keys on (UNIQUE Lot_Number/SAP_Code/Site_ID). A
partial approval is `approved_qty < submitted_qty`; whatever is left is
`rejected_qty` and needs a reason.

The UNIQUE on (source_type, source_ref, SAP_Code, Lot_Number) is the part
that matters operationally: the inspection is OPENED by a trigger inside the
warehouse-receive and DN-receive transactions, and those can be retried. A
second inspection for the same physical goods would split the approved
quantity across two rows and let the issuance guard authorise the same units
twice. The constraint makes the trigger idempotent instead of relying on
every caller to check first.

**qc_transfer_requests** carries the HOD-raises / Admin-approves flow for
moving a QC account between sites. It is two-step because approving it
rewrites `users.Site_ID`, and role/site/warehouse ride INSIDE the 15-minute
access token (audit A03-F9) — so approval is also where the account's
sessions get revoked. A one-step transfer would leave the QC holding their
old site's authority for up to a quarter of an hour.

**mtc_documents gains five columns.** The certificate used to be a purely
site-side artefact bolted to one staged receipt (`pending_receipt_id`). The
mandatory gate has moved upstream to warehouse goods-in and DN creation, so
the row now has to be able to answer "does THIS warehouse hold a certificate
for THIS PO line", and later "which DN did it travel on".
`Material_Code_Ref` exists because `dn_items` carry `Material_Code` and no
SAP at all — matching on SAP alone would silently pass every DN line.

No foreign keys, matching the rest of this schema: the baseline declares
ZERO across 74 tables. The single FK in the codebase is
refresh_sessions.user_id, which needs ON DELETE CASCADE for a genuinely
different reason.

No indexes beyond the unique constraint. Rule 11 — an index is benchmarked
on an inflated clone before it is added, and four of eleven candidates were
rejected on evidence last time. These tables have zero rows today; the
candidates to MEASURE later are qc_inspections (Site_ID, SAP_Code, status)
for the issuance guard and (status) for the QC worklist.
"""
from alembic import op
import sqlalchemy as sa

revision = "b4d17c8e93a2"
down_revision = "a71e93b4c2f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qc_inspections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # Exactly one of these is set — an inspection happens at a site or at
        # a warehouse, never both. Enforced in the service, not by a CHECK:
        # the rest of this schema keeps that kind of rule in Python where the
        # error message can name the caller's mistake.
        sa.Column("Site_ID", sa.Text(), nullable=True),
        sa.Column("Warehouse_ID", sa.Text(), nullable=True),
        sa.Column("SAP_Code", sa.Text(), nullable=False),
        sa.Column("Material_Code", sa.Text(), nullable=True),
        sa.Column("Lot_Number", sa.Text(), nullable=True),
        # warehouse_receipt | dn_receipt | site_receipt
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("mtc_document_id", sa.Integer(), nullable=True),
        sa.Column("submitted_qty", sa.Float(), nullable=False),
        sa.Column("approved_qty", sa.Float(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("rejected_qty", sa.Float(), nullable=False,
                  server_default=sa.text("0")),
        # pending | approved | partially_approved | rejected
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("inspected_by", sa.Text(), nullable=True),
        sa.Column("inspected_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_type", "source_ref", "SAP_Code",
                            "Lot_Number", name="uq_qc_inspection_source"),
    )

    op.create_table(
        "qc_transfer_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("from_site", sa.Text(), nullable=True),
        sa.Column("to_site", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        # pending_admin | approved | rejected | cancelled
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'pending_admin'")),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    for col in (
        sa.Column("Warehouse_ID", sa.Text(), nullable=True),
        sa.Column("Material_Code_Ref", sa.Text(), nullable=True),
        sa.Column("po_item_id", sa.Integer(), nullable=True),
        sa.Column("DN_Number", sa.Text(), nullable=True),
        sa.Column("qc_inspection_id", sa.Integer(), nullable=True),
    ):
        op.add_column("mtc_documents", col)

    # Site_ID has been NOT NULL since the baseline, because the only caller
    # was a site Store Keeper attaching a certificate to their own receipt.
    # A WAREHOUSE certificate has no site — the goods have not been allocated
    # to one yet — so the constraint makes the new mandatory gate at
    # warehouse goods-in literally unsatisfiable.
    #
    # Relaxed rather than defaulted: writing 'HQ' or the warehouse's own id
    # into Site_ID would make a warehouse certificate look, to every existing
    # site-scoped query, like paperwork that already belongs to a site.
    # Exactly one of Site_ID / Warehouse_ID is set, enforced in entry.upload_mtc.
    op.alter_column("mtc_documents", "Site_ID",
                    existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Re-tightening would fail on any warehouse certificate written since the
    # upgrade, so park those at 'HQ' first — the downgrade is a schema revert,
    # not a licence to delete somebody's compliance documents.
    op.execute("UPDATE mtc_documents SET \"Site_ID\" = 'HQ' WHERE \"Site_ID\" IS NULL")
    op.alter_column("mtc_documents", "Site_ID",
                    existing_type=sa.Text(), nullable=False)
    for name in ("qc_inspection_id", "DN_Number", "po_item_id",
                 "Material_Code_Ref", "Warehouse_ID"):
        op.drop_column("mtc_documents", name)
    op.drop_table("qc_transfer_requests")
    op.drop_table("qc_inspections")
