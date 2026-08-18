"""QSEP slice 6 — auto-drafted DNs, urgent delivery, and stored PR/PO scans

Revision ID: e6a91c37b208
Revises: d2f84b19e57c
Create Date: 2026-08-11

**urgency on po_reschedule_requests.** "Urgent delivery" is a reschedule to
an EARLIER date, so it reuses the table that already carries HOD/WH →
Logistics reschedules with a mandatory reason and an approve step that
pushes the new date onto the PO. A second table would have duplicated all of
that to add one word. The column exists so the dispatch can pick
severity="critical", which is what makes the message bypass the 16:00
evening digest — a request to bring a delivery forward is worthless if it
arrives after the working day it was meant to change.

**auto_generated / source_assignment_id on delivery_notes.** A DN drafted by
the system after warehouse goods-in has to be distinguishable from one a
human prepared: it is still a DRAFT and still needs a person to submit it,
but a warehouse clerk opening their queue deserves to know which rows they
did not create. `source_assignment_id` is the trace back to the receipt that
produced it.

**source_attachment_id on pr_master and purchase_orders.** The document the
figures were read from. Until now `/ai/extract/{pr,po}` read the uploaded
bytes, parsed them and THREW THEM AWAY — nothing was stored, so a PR created
from a scan had no scan. `purchase_orders` already has
attachment_blob/name/mime from the legacy import; those columns keep the
rows they already hold and nothing new is written to them. One store
(`entry_attachments`) from here on.

No FKs, matching the schema. No indexes: rule 11 — benchmarked before added,
and these are lookups by primary key on tables in the hundreds of rows.
"""
from alembic import op
import sqlalchemy as sa

revision = "e6a91c37b208"
down_revision = "d2f84b19e57c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("po_reschedule_requests",
                  sa.Column("urgency", sa.Text(), nullable=False,
                            server_default=sa.text("'normal'")))
    op.add_column("delivery_notes",
                  sa.Column("auto_generated", sa.Integer(), nullable=False,
                            server_default=sa.text("0")))
    op.add_column("delivery_notes",
                  sa.Column("source_assignment_id", sa.Integer(), nullable=True))
    op.add_column("pr_master",
                  sa.Column("source_attachment_id", sa.Integer(), nullable=True))
    op.add_column("purchase_orders",
                  sa.Column("source_attachment_id", sa.Integer(), nullable=True))

    # ⚠️ entry_attachments."Site_ID" has been NOT NULL since the baseline,
    # because every uploader was a site Store Keeper. A PO scan is uploaded by
    # LOGISTICS, who are unscoped by design and raise POs across every site —
    # so the constraint made storing one impossible.
    #
    # Relaxed rather than defaulted, and NULL already behaves correctly under
    # the existing Document Library scoping: `list_attachments` filters
    # `Site_ID == <site>` for a scoped caller, which a NULL never matches
    # (invisible — fail-closed), while an unscoped caller gets no filter and
    # sees it. Writing 'HQ' instead would make a cross-site purchase document
    # claim to belong to one particular site.
    op.alter_column("entry_attachments", "Site_ID",
                    existing_type=sa.Text(), nullable=True)

    # Settings, all idempotent so a re-run is a no-op.
    #   auto_draft_dn      — the warehouse that batches its shipments turns
    #                        this off; on by default because the common case
    #                        is one DN per receipt and re-keying it is waste.
    #   ocr_purchase_scans — the vision lane for scanned PR/POs. Separate
    #                        from ai_ocr_enabled so a site can keep
    #                        handwriting OCR while turning off the (slower,
    #                        heavier) purchase-document lane, or vice versa.
    data_upgrade(op.get_bind())


def data_upgrade(conn) -> None:
    """DATA step — see cutover_migrate.run_data_migrations.

    Seed rows, not schema: `create_all` builds the app_settings TABLE but
    nothing puts these two keys in it, so a cut-over box would come up with
    both features silently absent. ON CONFLICT DO NOTHING makes a re-run a
    no-op and never overrides an operator's later choice.
    """
    for key, value in (("auto_draft_dn", "1"),
                       ("ocr_purchase_scans", "1")):
        conn.execute(sa.text(
            "INSERT INTO app_settings (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO NOTHING").bindparams(k=key, v=value))


def downgrade() -> None:
    # Park cross-site scans at 'HQ' before re-tightening: failing the
    # downgrade on rows the upgrade legitimately created is worse than a
    # slightly wrong site on a document nobody is looking for.
    op.execute("UPDATE entry_attachments SET \"Site_ID\" = 'HQ' "
               'WHERE "Site_ID" IS NULL')
    op.alter_column("entry_attachments", "Site_ID",
                    existing_type=sa.Text(), nullable=False)
    op.execute("DELETE FROM app_settings WHERE key IN "
               "('auto_draft_dn', 'ocr_purchase_scans')")
    op.drop_column("purchase_orders", "source_attachment_id")
    op.drop_column("pr_master", "source_attachment_id")
    op.drop_column("delivery_notes", "source_assignment_id")
    op.drop_column("delivery_notes", "auto_generated")
    op.drop_column("po_reschedule_requests", "urgency")
