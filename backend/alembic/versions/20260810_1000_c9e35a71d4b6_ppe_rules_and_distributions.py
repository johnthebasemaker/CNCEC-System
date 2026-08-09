"""QSEP slice 4 — PPE usable-time rules and the distribution ledger

Revision ID: c9e35a71d4b6
Revises: b4d17c8e93a2
Create Date: 2026-08-10

WHAT COUNTS AS PPE, AND WHY IT IS TWO THINGS

The operator pushed a real `PPE` category into `inventory` on 2026-08-09 (9
SAPs today, beside the 41 that stayed `Safety`), so the category is a real
signal and the UI filters on it. But the CATEGORY cannot be the whole answer,
because it carries no usable time: "Safety Shoes" and "Goggles" are both PPE
and wear out at six months and three. `ppe_rules` is where the SK states the
number.

So the two are used for different jobs, and mixing them up is the mistake to
avoid:

  * `inventory."Category" = 'PPE'`  →  which items OFFER the PPE flow (UI)
  * a `ppe_rules` row                →  which items have a usable time (maths)

A PPE-category item with no rule is still distributed and still recorded —
it simply has no expiry, and the forecast cannot see it. That is a data-entry
gap the rules page surfaces, not an error.

THE UNIQUE INDEX IS ON COALESCE(Site_ID, ''), NOT ON (SAP_Code, Site_ID)

Postgres treats NULLs as distinct, so a plain UNIQUE(SAP_Code, Site_ID) would
allow unlimited duplicate GLOBAL rules — every one of them matching, with the
resolver picking whichever the planner returned first. That is the same class
of bug the '' scoping rules in this codebase exist to prevent, and here it
would silently change how long a pair of boots is deemed to last.

DATES ARE TEXT ISO, DELIBERATELY

`issued_on` / `expires_on` are text, matching receipts."Date",
lots."Received_Date" and pr_master."Delivery_Date". Every date in this schema
is text ISO; a lone DATE column reads as more correct in isolation and is
worse in aggregate, because the comparisons that matter here are against
those columns. A mixed convention is how the returnables timezone bug
happened.

`usable_days_applied` and `expires_on` are BOTH stored, rather than expiry
being derived on read. The rule may change later — an operator shortening
Safety Shoes from 6 months to 4 must not retroactively rewrite when the boots
already on someone's feet were deemed to expire.

No FKs, matching the schema. No indexes yet: rule 11 says an index is
benchmarked on an inflated clone before it is added, and this table has zero
rows. Candidates to MEASURE later are (employee_id_number, status) for the
per-person history and (Site_ID, expires_on) for the forecast window.
"""
from alembic import op
import sqlalchemy as sa

revision = "c9e35a71d4b6"
down_revision = "b4d17c8e93a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ppe_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("SAP_Code", sa.Text(), nullable=False),
        # NULL = the global default for this material; a site row overrides it.
        sa.Column("Site_ID", sa.Text(), nullable=True),
        sa.Column("usable_days", sa.Integer(), nullable=False),
        sa.Column("requires_safety_doc", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        'CREATE UNIQUE INDEX ux_ppe_rules_sap_site '
        'ON ppe_rules ("SAP_Code", COALESCE("Site_ID", \'\'))')

    op.create_table(
        "ppe_distributions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("Site_ID", sa.Text(), nullable=False),
        # The PERSON, not the employment record. employees."ID_Number" is
        # globally unique, so this survives a site transfer intact — which is
        # what makes "PPE history carries over" work at all (ruling R1).
        # Keying on mh_employees.id would fork the history on transfer,
        # because that table is UNIQUE(Site_ID, Employee_Code).
        sa.Column("employee_id_number", sa.Text(), nullable=False),
        sa.Column("employee_name", sa.Text(), nullable=True),
        sa.Column("SAP_Code", sa.Text(), nullable=False),
        sa.Column("Material_Code", sa.Text(), nullable=True),
        sa.Column("Description", sa.Text(), nullable=True),
        sa.Column("Lot_Number", sa.Text(), nullable=True),
        sa.Column("Qty", sa.Float(), nullable=False),
        sa.Column("issued_on", sa.Text(), nullable=False),
        # Both stored: the rule may change, history must not move with it.
        sa.Column("usable_days_applied", sa.Integer(), nullable=True),
        sa.Column("expires_on", sa.Text(), nullable=True),
        sa.Column("safety_doc_id", sa.Integer(), nullable=True),
        sa.Column("replaces_distribution_id", sa.Integer(), nullable=True),
        sa.Column("early_replacement", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("early_reason", sa.Text(), nullable=True),
        # Staged first, committed later: the SK hands the boots over now and
        # the HOD approves the paperwork afterwards, so the row exists from
        # the moment it is staged (that is what stops a second pair being
        # issued in between) and is voided if the HOD rejects.
        sa.Column("pending_issue_id", sa.Integer(), nullable=True),
        sa.Column("consumption_id", sa.Integer(), nullable=True),
        # active | replaced | expired | returned | void
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'active'")),
        sa.Column("issued_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ppe_distributions")
    op.execute("DROP INDEX IF EXISTS ux_ppe_rules_sap_site")
    op.drop_table("ppe_rules")
