"""hot-path indexes on the ledgers and the audit log

Revision ID: e7c3b95a41d2
Revises: a4e9b1c73f28
Create Date: 2026-08-04

Before this migration the three ledger tables and `system_audit_log` carried
NOTHING but their primary keys, while every stock, dashboard and report query
filters them by SAP code, site or date. Measured on a clone of the live
database inflated to a plausible two-year volume (260k receipts, 240k
consumption, 429k audit rows):

    query                                before     after
    receipts by (SAP_Code, Site_ID)      13.9 ms    0.7 ms     20x
    consumption by Date >= cutoff        27.6 ms    0.3 ms     92x
    consumption by Date + Site           18.7 ms    2.5 ms      7x
    receipts JOIN inventory ON TRIM(SAP) 128.8 ms   22.4 ms     6x
    audit by action_type                 11.5 ms    2.0 ms      6x

Cost: ~10 MB of index at that volume, and 88 ms to insert 20,000 ledger rows
(4.4 microseconds per row) — the write path does not notice.

Four further candidates were BENCHMARKED AND REJECTED rather than added on
principle, because an unused index is pure write-amplification and disk:

  * `system_audit_log (id DESC)` and `(username, id DESC)` — 9.4 MB and 9.6 MB
    for **zero** planner uses. The primary key already serves `ORDER BY id
    DESC LIMIT n` by scanning backwards.
  * `receipts (TRIM("SAP_Code"))` / `consumption (TRIM(...))` — the planner
    prefers the `(SAP_Code, Site_ID)` index for the TRIM join anyway.
  * `inventory (TRIM("SAP_Code"))` — `inventory` is a 442-row master table; a
    sequential scan of it is already faster than an index descent.

Ledger tables must never gain a UNIQUE constraint (the same date/SAP/quantity
line can legitimately repeat) — every index here is deliberately non-unique.
"""
from alembic import op

revision = "e7c3b95a41d2"
down_revision = "a4e9b1c73f28"
branch_labels = None
depends_on = None


# (index name, table, columns) — non-unique, all of them.
_INDEXES = [
    # Stock maths and the material card both filter a ledger by code + site.
    ("ix_receipts_sap_site", "receipts", ["SAP_Code", "Site_ID"]),
    ("ix_consumption_sap_site", "consumption", ["SAP_Code", "Site_ID"]),
    ("ix_returns_sap_site", "returns", ["SAP_Code", "Site_ID"]),
    # Dashboards, burn-rate and every "last N days" report window on Date.
    ("ix_receipts_date", "receipts", ["Date"]),
    ("ix_consumption_date", "consumption", ["Date"]),
    ("ix_returns_date", "returns", ["Date"]),
    # The audit page and several suites filter by action_type.
    ("ix_audit_action_type", "system_audit_log", ["action_type"]),
]


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols, unique=False)


def downgrade() -> None:
    for name, table, _cols in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
