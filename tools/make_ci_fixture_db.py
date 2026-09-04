#!/usr/bin/env python3
"""
tools/make_ci_fixture_db.py — a legacy-shaped SQLite database CI can actually have.

    python tools/make_ci_fixture_db.py                 # → ci_fixture.db
    python tools/make_ci_fixture_db.py --out /tmp/x.db

⚠️ WHY THIS EXISTS: `gi_database.db` IS NOT IN GIT, AND HAS NOT BEEN SINCE
2026-07-26.

Commit `a09da0b` — "security(secrets): untrack tracked SQLite databases and
archived backups" — removed it deliberately. It holds real employee names, real
stock, real vendors and a customer's project; `bin/backup_db.sh` states the
decision plainly ("it lives only on this disk, and it is never in git") and
`.gitignore:13` enforces it.

But `postgres-dual-ci.yml` still read it from THREE steps — `dual_ci`,
`parity_check`, and `service_tests` (via `testdb._load` → `cutover_migrate`) —
and the workflow's own comment still described "the *committed* gi_database.db".
So from 2026-07-26 those steps could never have passed on a runner: they exit
with `source not found` before doing anything. Nobody saw it for six weeks
because `legacy/bug_check.py` failed FIRST on all 30 recorded runs. Fixing that
in Phase 11a did not break this — it advanced the job far enough to reach what
was already broken.

⚠️ THE FIX IS NOT TO COMMIT THE DATABASE. That would reverse a deliberate
security commit and publish the operator's employee register to a git remote.

────────────────────────────────────────────────────────────────────────────
WHAT THIS FIXTURE IS FOR, AND WHAT IT IS NOT FOR

**It is for `tools/dual_ci.py` and `tools/parity_check.py`** — the two steps this
workflow is named for. They validate the DATA LAYER: schema creation from
`models.py`, the copy path, type coercion, sequence fixups, view creation and
dialect, per-table row-count parity, and five semantic aggregates. All of that is
exercised by a legacy-shaped database with a handful of rows; the operator's
1,474 real ones prove nothing extra there, and a fixture cannot leak. Verified:
both pass against a real PostgreSQL 16 on this fixture.

⚠️ **IT IS NOT ENOUGH FOR `backend/api/service_tests.py`, AND THAT IS A FINDING
RATHER THAN A GAP IN THIS FILE.** That suite hardcodes identifiers out of the
operator's master data — four login accounts, five SAP codes, employee `30001`,
seven `GI-*` material codes, and enough SME scopes for the man-hours scorecard
and auto-draft to mean anything. The rows below cover the unambiguous ones.

Reconstructing the REST synthetically was attempted and abandoned ON EVIDENCE:
inventing the seven material codes and a second SME scope took the suite from 6
failures to 16, because the invented rows changed what assertions like
"2 matched / 1 unmatched" and "auto-draft save → 2 estimates" actually mean.
Every guessed row is a guess about INTENT, and a suite that tests my guesses is
worse than one that is honestly skipped.

That leaves a decision only the operator can make — written up in
`docs/PROJECT_STATUS.md` §1b. Rule 15 currently claims the suite is rebuilt from
`gi_database.db` and that "that file is in git and is itself a gate, so every
machine and CI start from identical rows". It is not in git, so that guarantee
has been false since 2026-07-26 and the suite has only ever passed on one laptop.

⚠️ THE SCHEMA IS BUILT BY `legacy.database.init_db()`, NOT RESTATED HERE. A
hand-written CREATE TABLE list would be a second copy of the legacy schema that
agrees with the first until somebody adds a column — and noticing schema drift is
the whole point of dual-CI, so a fixture that could drift would defeat the
harness it feeds.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_OUT = _ROOT / "ci_fixture.db"

# ⚠️ HARDCODED IN `backend/api/service_tests.py` — `token("admin", "admin2026")`
# and its three siblings. Roles and sites mirror the live master; the PASSWORDS
# are the suite's own well-known test values, hashed here at build time. No hash
# is ever copied out of the real database.
SUITE_USERS = [
    ("admin",      "admin2026", "admin",        None),
    ("hod",        "hod2026",   "hod",          "CNCEC"),
    ("supervisor", "super2026", "supervisor",   "CNCEC"),
    ("worker",     "floor2026", "store_keeper", "CNCEC"),
]

# The SAP codes the suite names in its own source, with the UOM and Category the
# live master carries. Identifiers and classifications, not data.
SUITE_SAPS = [
    ("1001", "EQUIPMENTS/TOOLS"), ("1002", "EQUIPMENTS/TOOLS"),
    ("1003", "EQUIPMENTS/TOOLS"), ("1038", "Surface Shields"),
    ("9000", "Consumable"),
]

SEED_ITEMS = [
    ("SAP-001", "Widget A",      "MC-001", "PCS", "Consumable",      100, 10.0),
    ("SAP-002", "Bolt M8",       "MC-002", "PCS", "Consumable",       50,  2.0),
    ("SAP-003", "O-Ring Rubber", "MC-003", "PCS", "Rubber materials", 20,  5.0),
    ("SAP-004", "Drill Bit",     "MC-004", "PCS", "Tools",             0, 25.0),
    ("SAP-005", "Test Kit",      "MC-005", "SET", "QC items",          5, 100.0),
]


def build(out_path: pathlib.Path) -> dict:
    """Create a fresh legacy-shaped SQLite database at `out_path`."""
    if out_path.exists():
        out_path.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ⚠️ IMPORT ORDER IS THE MECHANISM, exactly as in `legacy/bug_check.py`:
    # `config.DB_FILE` and `database.DB_FILE` are read at connect time, so they
    # must be pointed at the fixture BEFORE anything opens a connection. Getting
    # this wrong writes the fixture into the operator's live database.
    legacy = str(_ROOT / "legacy")
    if legacy not in sys.path:
        sys.path.insert(0, legacy)
    os.environ.pop("DATABASE_URL", None)          # this is the SQLite side

    import config
    config.DB_FILE = str(out_path)
    import database
    database.DB_FILE = str(out_path)

    database.init_db()

    import bcrypt

    conn = database.get_connection()
    try:
        c = conn.cursor()

        for uname, pw, role, site in SUITE_USERS:
            # ⚠️ `rounds=4`, NOT the production cost. This fixture is rebuilt on
            # every CI run and read by nobody outside it; the default cost adds
            # about a second per account for security nobody benefits from. The
            # APP's own hashing is untouched — this only mints fixture rows.
            c.execute("INSERT OR IGNORE INTO users "
                      "(username, password_hash, role, Site_ID) VALUES (?,?,?,?)",
                      (uname,
                       bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode(),
                       role, site))
        for u, role, site in (("ci_admin", "admin", "HQ"),
                              ("ci_sk", "store_keeper", "HQ"),
                              ("ci_sk_b", "store_keeper", "SITE_B")):
            c.execute("INSERT OR IGNORE INTO users "
                      "(username, password_hash, role, Site_ID, Phone_Number) "
                      "VALUES (?,?,?,?,?)",
                      (u, "$2b$12$placeholder", role, site, "+966500000000"))

        for sap, desc, mc, uom, cat, opening, cost in SEED_ITEMS:
            c.execute("INSERT OR IGNORE INTO inventory "
                      '("SAP_Code","Equipment_Description","Material_Code","UOM",'
                      '"Category","Opening_Stock","Unit_Cost","Site_ID") '
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (sap, desc, mc, uom, cat, opening, cost, "HQ"))
        for sap, cat in SUITE_SAPS:
            c.execute("INSERT OR IGNORE INTO inventory "
                      '("SAP_Code","Equipment_Description","Material_Code","UOM",'
                      '"Category","Opening_Stock","Unit_Cost","Site_ID") '
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (sap, f"CI Item {sap}", f"MC-{sap}", "Each", cat,
                       500, 1.0, "CNCEC"))

        for site in ("CNCEC", "ZZ"):
            c.execute("INSERT OR IGNORE INTO system_settings (category, value) "
                      "VALUES (?,?)", ("Site", site))
        c.execute('INSERT INTO employees ("ID_Number","Name","Department",'
                  '"status","Site_ID") VALUES (?,?,?,?,?)',
                  ("30001", "CI Fixture Worker", "Production", "active", "CNCEC"))

        # ⚠️ NON-ZERO MOVEMENTS ARE THE POINT. `dual_ci.SEMANTIC_CHECKS` sums
        # Quantity over receipts / consumption / returns; a fixture with none of
        # them makes every one of those checks compare 0 to 0, which passes
        # against any schema at all and measures nothing.
        # `Expiry_Date` is what `v_expiring_stock` selects on.
        c.execute('INSERT INTO receipts ("SAP_Code","Quantity","Date","Site_ID",'
                  '"Supplier","Lot_Number","Expiry_Date") VALUES (?,?,?,?,?,?,?)',
                  ("SAP-001", 40.0, "2026-01-05", "HQ", "CI Vendor",
                   "LOT-CI-1", "2027-01-05"))
        c.execute('INSERT INTO receipts ("SAP_Code","Quantity","Date","Site_ID",'
                  '"Supplier","Lot_Number","Expiry_Date") VALUES (?,?,?,?,?,?,?)',
                  ("SAP-003", 12.5, "2026-01-06", "HQ", "CI Vendor",
                   "LOT-CI-2", "2026-02-01"))
        c.execute('INSERT INTO consumption ("SAP_Code","Quantity","Date",'
                  '"Site_ID","Issued_To","Lot_Number") VALUES (?,?,?,?,?,?)',
                  ("SAP-001", 7.5, "2026-01-07", "HQ", "ci_sk", "LOT-CI-1"))
        c.execute('INSERT INTO returns ("SAP_Code","Quantity","Date","Site_ID",'
                  '"Reason") VALUES (?,?,?,?,?)',
                  ("SAP-001", 2.0, "2026-01-08", "HQ", "CI fixture return"))
        # SAP 1001 with ledger history, so an item that must refuse deletion has
        # something to refuse over.
        c.execute('INSERT INTO receipts ("SAP_Code","Quantity","Date","Site_ID",'
                  '"Supplier") VALUES (?,?,?,?,?)',
                  ("1001", 25.0, "2026-01-09", "CNCEC", "CI Vendor"))
        c.execute('INSERT INTO consumption ("SAP_Code","Quantity","Date",'
                  '"Site_ID","Issued_To") VALUES (?,?,?,?,?)',
                  ("1001", 3.0, "2026-01-10", "CNCEC", "worker"))

        # ⚠️ PLAIN INSERT, NOT `INSERT OR IGNORE`. The first draft used OR IGNORE
        # here and the row silently did not land — `lots.Status` carries a CHECK
        # constraint ('open','exhausted','expired','disposed','quarantine') and
        # the draft passed 'active'. OR IGNORE swallowed the IntegrityError,
        # leaving `v_lot_balance SUM(Remaining_Qty)` comparing 0 to 0. Switching
        # to a plain INSERT named the cause in one run. OR IGNORE is right for
        # idempotent seed rows and wrong for anything an assertion depends on.
        c.execute('INSERT INTO lots ("Lot_Number","SAP_Code","Site_ID",'
                  '"Received_Date","Expiry_Date","Supplier","Status") '
                  "VALUES (?,?,?,?,?,?,?)",
                  ("LOT-CI-1", "SAP-001", "HQ", "2026-01-05", "2027-01-05",
                   "CI Vendor", "open"))

        # `/mh/meta` builds its dropdowns from the frozen SME masters. ⚠️ NO
        # `SAP_Code` on `sme_recipe`: the FROZEN legacy table predates that
        # column, which alembic a4e9b1c73f28 added on the Postgres side (rule 1's
        # component identity). Writing it into the SQLite source raises
        # `no such column` — and that difference between the two schemas is
        # exactly the sort of thing dual-CI exists to notice.
        c.execute('INSERT OR IGNORE INTO sme_equipment ("Equipment_Tag_No",'
                  '"Site_ID","Location","Lining_System_Code",'
                  '"Surface_Area_SQM","Equipment_Total_SQM") '
                  "VALUES (?,?,?,?,?,?)",
                  ("TNK-CI-1", "CNCEC", "CI Yard", "LSC1", 120.0, 120.0))
        c.execute('INSERT OR IGNORE INTO sme_recipe ("Lining_System_Code",'
                  '"Material_Code","For_1_SQM","UOM") VALUES (?,?,?,?)',
                  ("LSC1", "MC-1001", 1.5, "Each"))

        conn.commit()
        database.log_audit_action("ci_admin", "CI_FIXTURE_BUILD", "inventory",
                                  "synthetic fixture for dual-CI")
    finally:
        conn.close()

    conn = database.get_connection()
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("users", "inventory", "receipts", "consumption",
                            "returns", "system_audit_log", "lots", "employees",
                            "sme_equipment", "sme_recipe")}
        # ⚠️ THE FIXTURE CHECKS ITSELF, because every way it can go wrong is
        # SILENT downstream. An empty table makes dual_ci's parity check for it
        # compare 0 to 0 — passing against any schema, measuring nothing. Failing
        # here makes it a build error with a name on it instead.
        empty = [t for t, n in counts.items() if not n]
        if empty:
            raise RuntimeError(
                f"fixture built with EMPTY table(s): {empty}. Every dual_ci "
                f"parity check over them would compare 0 to 0 and pass "
                f"vacuously — fix the seed rather than shipping the fixture.")
        for view in ("v_site_stock", "v_lot_balance", "v_expiring_stock"):
            n = conn.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
            counts[view] = n
            if not n:
                raise RuntimeError(
                    f"fixture produced an EMPTY view {view} — dual_ci's "
                    f"view-parity check over it would be vacuous")
    finally:
        conn.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"output path (default: {DEFAULT_OUT.name})")
    args = ap.parse_args(argv)

    out = pathlib.Path(args.out).resolve()
    # ⚠️ REFUSE TO OVERWRITE THE REAL DATABASE. The whole reason this file exists
    # is that the live one must never be somewhere CI can reach; the mirror of
    # that is a generator that can never be pointed at it.
    if out.name == "gi_database.db":
        print("❌ refusing to write to gi_database.db — that is the live legacy "
              "database, not a fixture", file=sys.stderr)
        return 2

    counts = build(out)
    print(f"▶ wrote {out} ({out.stat().st_size // 1024} KB)")
    print("  " + " · ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
