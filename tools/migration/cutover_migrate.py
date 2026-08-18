#!/usr/bin/env python3
"""
scripts/migration/cutover_migrate.py — PRODUCTION CUTOVER: legacy SQLite → PostgreSQL.

The one-shot, heavily-verified data migration for cutover day. It layers the
production concerns on top of the proven core copier
(`backend/migrate_sqlite_to_postgres.py`, used by dual_ci on every reload):

  PRE-FLIGHT   source integrity_check · target reachable · refuses a NON-EMPTY
               target unless --wipe (never clobbers silently)
  LOAD         full schema (models.py = the contract) + data, chunked; the 3
               rowid-ledger tables keep id := sqlite rowid so posted_txn_ref
               ('C:{rowid}'/'R:{rowid}') stays valid; SQLite's loose typing is
               coerced (junk in numeric/date cols → NULL, counted + reported);
               PG sequences reset past MAX(id)
  POST-LOAD    alembic_version stamped to the current head (so future Alembic
               migrations apply cleanly) · users/pending_users/employees phone
               numbers normalised to the +E.164 project rule (non-conforming
               values reported, never destroyed)
  VERIFY       per-table row-count parity · dual_ci semantic aggregate checks
               (stock identity, valuation, FEFO lots, MH hours, SME SQM) ·
               UOM-conversion integrity (Factor>0, no dupes, SAPs known) ·
               soft-FK orphan scan (ledger SAP→inventory, po_items→PO,
               dn_items→DN, smr_items→SMR, lots→inventory)

Exit code 0 only when EVERY blocking check passes. Orphan scans are advisory
(the legacy DB is deliberately loose there) — they print loudly but only fail
the run with --strict.

Usage
-----
  # Cutover (target from --target or $DATABASE_URL):
  .venv/bin/python scripts/migration/cutover_migrate.py \
      --source gi_database.db \
      --target postgresql+psycopg2://gihub:<pw>@localhost:5432/gihub --wipe

  # Verify an already-loaded target without reloading:
  .venv/bin/python scripts/migration/cutover_migrate.py --verify-only \
      --source gi_database.db --target postgresql+psycopg2://…

See scripts/migration/README.md for the full cutover-day runbook.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

# Phase B layout: tools/migration/ — siblings migrate/dual_ci live in tools/,
# the schema contract (models.py) stays in backend/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.dirname(_HERE))            # tools/ (migrate, dual_ci)
sys.path.insert(0, os.path.join(_ROOT, "backend"))    # models
sys.path.insert(0, _ROOT)                             # repo root

import models  # noqa: E402
import migrate_sqlite_to_postgres as mig  # noqa: E402
from dual_ci import SEMANTIC_CHECKS  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

GOOD, BAD, WARN = "✅", "❌", "⚠️"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _alembic_head() -> str | None:
    """Current single head from backend/alembic (None if unresolvable)."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(os.path.join(_ROOT, "backend", "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(_ROOT, "backend", "alembic"))
        heads = ScriptDirectory.from_config(cfg).get_heads()
        return heads[0] if len(heads) == 1 else None
    except Exception as e:  # noqa: BLE001
        print(f"{WARN} could not resolve the alembic head: {type(e).__name__}: {e}")
        return None


def _normalize_phone(raw: str) -> str | None:
    """+E.164 project rule (mirrors backend.api.auth.normalize_phone without the
    HTTP dependency): 8–15 digits, no leading 0 → '+<digits>'; else None."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if 8 <= len(digits) <= 15 and not digits.startswith("0"):
        return "+" + digits
    return None


# --------------------------------------------------------------------------- #
# stages
# --------------------------------------------------------------------------- #
def preflight(source: str, target_url: str, wipe: bool) -> list[str]:
    """Blocking pre-flight problems (empty list = go)."""
    problems: list[str] = []
    if not os.path.exists(source):
        return [f"source DB not found: {source}"]
    size_mb = os.path.getsize(source) / 1e6
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        integ = src.execute("PRAGMA integrity_check").fetchone()[0]
        if integ != "ok":
            problems.append(f"SQLite integrity_check: {integ}")
        n_tables = src.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        print(f"   source: {source} · {size_mb:.1f} MB · {n_tables} tables · integrity ok")
    finally:
        src.close()

    engine = create_engine(target_url)
    try:
        with engine.connect() as conn:
            if engine.dialect.name != "postgresql":
                print(f"{WARN} target is {engine.dialect.name}, not postgresql "
                      "(fine for a dry-run, wrong for production)")
            existing = conn.execute(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public'" if engine.dialect.name == "postgresql"
                else "SELECT count(*) FROM sqlite_master WHERE type='table'")).scalar()
            print(f"   target: {engine.url.render_as_string(hide_password=True)} "
                  f"· {existing} existing table(s)")
            if existing and not wipe:
                problems.append(
                    f"target already has {existing} table(s) — refusing to load over it. "
                    "Re-run with --wipe to drop-and-reload (make sure this is the right DB!).")
    except Exception as e:  # noqa: BLE001
        problems.append(f"cannot reach target: {type(e).__name__}: {e}")
    finally:
        engine.dispose()
    return problems


def stamp_alembic(target_url: str) -> bool:
    """Write alembic_version = current head so future migrations apply."""
    head = _alembic_head()
    if not head:
        print(f"{WARN} alembic head unresolved — NOT stamped "
              "(run `alembic stamp head` manually before the next migration)")
        return False
    engine = create_engine(target_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL, "
                " CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"))
            conn.execute(text("DELETE FROM alembic_version"))
            conn.execute(text("INSERT INTO alembic_version (version_num) "
                              "VALUES (:v)"), {"v": head})
        print(f"{GOOD} alembic_version stamped → {head}")
        return True
    finally:
        engine.dispose()


def run_data_migrations(target_url: str) -> dict:
    """Run every migration's DATA step against a freshly loaded target.

    ⚠️ THE GAP THIS CLOSES. The load builds the schema from `models.py` with
    `create_all` and then STAMPS alembic_version to head. That is right for
    SCHEMA — models.py is the contract — but it means no migration ever
    *executes*, so every DATA step inside one is skipped. Those steps are not
    decoration: they normalise comma-list SAP codes into the component identity
    the whole SME layer pools on, seed the two app_settings keys that switch
    procurement automation on, and repair rows the legacy database left blank.
    A cut-over box was coming up with a correct schema and uncorrected data,
    and nothing said so.

    The contract: a migration whose upgrade() carries DML exposes
    `data_upgrade(conn)`, and upgrade() calls it. Both paths then run the same
    code — ordinary `alembic upgrade` through upgrade(), a cutover through
    here — so the two cannot drift. Every step must be idempotent; this runs
    AFTER a load that may already satisfy some of them.

    `verify_data_migration_contract` (below) fails the run when a migration
    carries DML and does not declare a step, so the gap cannot come back
    quietly the next time somebody writes one.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(os.path.join(_ROOT, "backend", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_ROOT, "backend", "alembic"))
    script = ScriptDirectory.from_config(cfg)
    # oldest → newest: a later step may depend on an earlier one's correction
    revs = list(reversed(list(script.walk_revisions())))

    ran, skipped, failed = [], [], []
    engine = create_engine(target_url)
    try:
        for rev in revs:
            mod = rev.module
            fn = getattr(mod, "data_upgrade", None)
            if fn is None:
                skipped.append(rev.revision)
                continue
            try:
                # One transaction per step: a failure names its own revision
                # instead of rolling back every correction before it.
                with engine.begin() as conn:
                    fn(conn)
                ran.append(rev.revision)
            except Exception as e:
                failed.append((rev.revision, f"{type(e).__name__}: {e}"))
    finally:
        engine.dispose()

    if ran:
        print(f"{GOOD} data steps run: {len(ran)} ({', '.join(ran)})")
    if not ran and not failed:
        print(f"{WARN} no migration declared a data step — expected at least one")
    for rev, err in failed:
        print(f"{BAD} data step {rev} FAILED: {err}")
    return {"ran": ran, "skipped": skipped, "failed": failed}


def verify_data_migration_contract(versions_dir: str | None = None) -> list[str]:
    """Fail the cutover when a migration carries DML but declares no data step.

    A source scan, not an import check: the point is to catch the migration
    somebody writes next year, and the failure has to be loud on cutover day
    rather than discovered in a stock report a week later.
    """
    import re

    vdir = versions_dir or os.path.join(_ROOT, "backend", "alembic", "versions")
    # DML inside upgrade() only — a downgrade's DML is not a cutover concern.
    # NB: no trailing \b after the UPDATE arm — \w matches the table name's
    # first letter and \b would then demand a boundary mid-word, so the arm
    # could never fire. The table may also be quoted ("employees").
    dml = re.compile(r'\b(?:INSERT\s+INTO|DELETE\s+FROM)\b|\bUPDATE\s+["\w]',
                     re.I)
    problems = []
    for name in sorted(os.listdir(vdir)):
        if not name.endswith(".py"):
            continue
        src = open(os.path.join(vdir, name), encoding="utf-8").read()
        up = src.split("def upgrade(")
        if len(up) < 2:
            continue
        body = up[1].split("\ndef ")[0]
        if dml.search(body) and "def data_upgrade(" not in src:
            problems.append(
                f"{name}: upgrade() carries DML but declares no data_upgrade() "
                f"— a cutover would skip it")
    return problems


def normalize_phones(target_url: str) -> dict:
    """users / pending_users / employees phone columns → +E.164. Values that
    cannot be normalised are LEFT UNTOUCHED and reported (no data destruction)."""
    plan = [("users", "Phone_Number", "username"),
            ("pending_users", "Phone_Number", "username"),
            ("employees", "Phone_Number", "ID_Number")]
    out: dict = {"normalized": 0, "unparseable": []}
    engine = create_engine(target_url)
    try:
        with engine.begin() as conn:
            for tname, col, label in plan:
                if tname not in models.Base.metadata.tables:
                    continue
                rows = conn.execute(text(
                    f'SELECT "{label}", "{col}" FROM {tname} '
                    f'WHERE "{col}" IS NOT NULL AND TRIM("{col}") <> \'\'')).all()
                for key, raw in rows:
                    norm = _normalize_phone(str(raw))
                    if norm is None:
                        out["unparseable"].append(f"{tname}.{key}={raw!r}")
                    elif norm != str(raw).strip():
                        conn.execute(text(
                            f'UPDATE {tname} SET "{col}" = :n WHERE "{label}" = :k'),
                            {"n": norm, "k": key})
                        out["normalized"] += 1
    finally:
        engine.dispose()
    flag = GOOD if not out["unparseable"] else WARN
    print(f"{flag} phone normalisation: {out['normalized']} value(s) rewritten to +E.164"
          + (f" · {len(out['unparseable'])} unparseable left as-is: "
             f"{out['unparseable'][:5]}{'…' if len(out['unparseable']) > 5 else ''}"
             if out["unparseable"] else ""))
    return out


# Tables a POST-LOAD data step legitimately adds rows to, so exact row-count
# parity with the source is the wrong assertion for them. Each entry names the
# step responsible; the check still fails on a SHORTFALL, which is what would
# actually indicate a lost copy.
POST_LOAD_ADDITIONS = {
    "app_settings": "e6a91c37b208 seeds auto_draft_dn + ocr_purchase_scans",
}


def verify(source: str, target_url: str, strict: bool) -> bool:
    """Row-count parity + semantic aggregates + UOM + soft-FK orphan scan."""
    ok = True
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    engine = create_engine(target_url)
    try:
        with engine.connect() as conn:
            # 1. Per-table row parity (source tables that exist in models).
            src_tables = {r[0] for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")}
            bad_tables, added = [], []
            for table in models.Base.metadata.sorted_tables:
                if table.name not in src_tables:
                    continue
                s = src.execute(f'SELECT COUNT(*) FROM "{table.name}"').fetchone()[0]
                t = conn.execute(text(f'SELECT COUNT(*) FROM {table.name}')).scalar()
                why = POST_LOAD_ADDITIONS.get(table.name)
                if why is not None:
                    # A gain is expected and explained; only a LOSS is a bug.
                    if int(t) < int(s):
                        bad_tables.append(
                            f"{table.name}: source={s} target={t} — FEWER rows "
                            f"than the source, which no data step can cause")
                    elif int(t) != int(s):
                        added.append(f"{table.name} +{int(t) - int(s)} ({why})")
                elif int(s) != int(t):
                    bad_tables.append(f"{table.name}: source={s} target={t}")
            print((GOOD if not bad_tables else BAD)
                  + f" table parity: {len(src_tables & set(models.Base.metadata.tables))} tables"
                  + (f" · MISMATCH {bad_tables}" if bad_tables else " — all row counts match")
                  + (f" · expected post-load additions: {'; '.join(added)}"
                     if added else ""))
            ok &= not bad_tables

            # 2. Semantic aggregates (same oracle dual_ci runs in CI).
            bad_sem = []
            for label, sql in SEMANTIC_CHECKS.items():
                s = src.execute(sql).fetchone()[0]
                t = conn.execute(text(sql)).scalar()
                if not _num_eq(s, t):
                    bad_sem.append(f"{label}: source={s} target={t}")
            print((GOOD if not bad_sem else BAD)
                  + f" semantic checks: {len(SEMANTIC_CHECKS)} aggregates"
                  + (f" · MISMATCH {bad_sem}" if bad_sem else " — all equal"))
            ok &= not bad_sem

            # 3. UOM-conversion integrity (the entry-form pack→base conversions).
            uom_bad = conn.execute(text(
                'SELECT COUNT(*) FROM uom_conversions WHERE "Factor" IS NULL '
                'OR "Factor" <= 0')).scalar()
            uom_dupes = conn.execute(text(
                'SELECT COUNT(*) FROM (SELECT TRIM("SAP_Code") AS s, "Pack_UOM" AS u, '
                'COUNT(*) AS n FROM uom_conversions GROUP BY 1, 2 HAVING COUNT(*) > 1) d'
            )).scalar()
            uom_orphans = conn.execute(text(
                'SELECT COUNT(*) FROM uom_conversions u WHERE TRIM(u."SAP_Code") '
                'NOT IN (SELECT TRIM("SAP_Code") FROM inventory)')).scalar()
            uom_ok = not (uom_bad or uom_dupes)
            print((GOOD if uom_ok else BAD)
                  + f" UOM conversions: {uom_bad} bad factor(s) · {uom_dupes} duplicate "
                  f"mapping(s) · {uom_orphans} orphan SAP(s)"
                  + ("" if uom_ok else " — FIX BEFORE GO-LIVE (entry-form conversion "
                     "would divide by zero / pick an arbitrary row)"))
            ok &= uom_ok

            # 4. Soft-FK orphan scan (advisory unless --strict; the legacy ledger
            #    never enforced these, so orphans are expected historical noise).
            orphan_sql = {
                "receipts→inventory": 'SELECT COUNT(*) FROM receipts r WHERE TRIM(r."SAP_Code") NOT IN (SELECT TRIM("SAP_Code") FROM inventory)',
                "consumption→inventory": 'SELECT COUNT(*) FROM consumption c WHERE TRIM(c."SAP_Code") NOT IN (SELECT TRIM("SAP_Code") FROM inventory)',
                "returns→inventory": 'SELECT COUNT(*) FROM returns x WHERE TRIM(x."SAP_Code") NOT IN (SELECT TRIM("SAP_Code") FROM inventory)',
                "lots→inventory": 'SELECT COUNT(*) FROM lots l WHERE TRIM(l."SAP_Code") NOT IN (SELECT TRIM("SAP_Code") FROM inventory)',
                "po_items→purchase_orders": 'SELECT COUNT(*) FROM po_items i WHERE i."PO_Number" NOT IN (SELECT "PO_Number" FROM purchase_orders)',
                "dn_items→delivery_notes": 'SELECT COUNT(*) FROM dn_items i WHERE i."DN_Number" NOT IN (SELECT "DN_Number" FROM delivery_notes)',
                "smr_items→smr": 'SELECT COUNT(*) FROM supervisor_material_request_items i WHERE i.request_id NOT IN (SELECT id FROM supervisor_material_requests)',
            }
            orphans = {}
            for label, sql in orphan_sql.items():
                try:
                    n = conn.execute(text(sql)).scalar()
                except Exception as e:  # noqa: BLE001 — table may be absent
                    n = f"skip ({type(e).__name__})"
                orphans[label] = n
            n_bad = sum(1 for v in orphans.values() if isinstance(v, int) and v > 0)
            flag = GOOD if n_bad == 0 else (BAD if strict else WARN)
            print(f"{flag} soft-FK orphan scan: "
                  + " · ".join(f"{k}={v}" for k, v in orphans.items()))
            if strict:
                ok &= n_bad == 0
    finally:
        src.close()
        engine.dispose()
    return ok


def _num_eq(a, b, tol: float = 1e-6) -> bool:
    try:
        return abs(float(a or 0) - float(b or 0)) <= tol
    except (TypeError, ValueError):
        return str(a) == str(b)


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 2)[1])
    ap.add_argument("--source", default=os.path.join(_ROOT, "gi_database.db"),
                    help="legacy SQLite file (default: repo gi_database.db)")
    ap.add_argument("--target", default=os.environ.get("DATABASE_URL", ""),
                    help="target SQLAlchemy URL (default: $DATABASE_URL)")
    ap.add_argument("--wipe", action="store_true",
                    help="drop + recreate the target schema before loading")
    ap.add_argument("--verify-only", action="store_true",
                    help="skip the load; run only the verification battery")
    ap.add_argument("--strict", action="store_true",
                    help="soft-FK orphans fail the run (default: advisory)")
    ap.add_argument("--chunk", type=int, default=1000)
    args = ap.parse_args(argv)

    if not args.target:
        print(f"{BAD} no target — pass --target or set DATABASE_URL")
        return 2

    print("== CUTOVER MIGRATION: legacy SQLite → PostgreSQL ==\n")
    print("[1] Pre-flight")
    problems = preflight(args.source, args.target, wipe=args.wipe or args.verify_only)
    # Blocking, and it runs BEFORE any write: a migration that carries DML and
    # declares no data step means this cutover would load a correct schema over
    # uncorrected data. Better to refuse than to discover it in a stock report.
    problems += verify_data_migration_contract()
    if problems:
        for p in problems:
            print(f"{BAD} {p}")
        return 2
    print(f"{GOOD} pre-flight clear\n")

    if not args.verify_only:
        print("[2] Load (schema + data + sequences)")
        report = mig.run_migration(args.source, args.target,
                                   chunk=args.chunk, wipe=args.wipe)
        bad = [t for t, i in report["tables"].items() if not i["ok"]]
        dropped = {t: i["dropped_columns"] for t, i in report["tables"].items()
                   if i.get("dropped_columns")}
        coerced = sum(i.get("coerced_to_null") or 0 for i in report["tables"].values())
        print((GOOD if not bad else BAD)
              + f" copied {len(report['tables'])} tables"
              + (f" · FAILED parity: {bad}" if bad else "")
              + (f" · {coerced} loose-typed value(s) → NULL" if coerced else ""))
        if dropped:
            print(f"{WARN} source-only columns NOT migrated: {dropped}")
        if not report["ok"] and bad:
            print(f"{BAD} aborting before post-load — fix the copy first")
            return 1

        print("\n[3] Post-load")
        stamp_alembic(args.target)
        # AFTER the stamp: the stamp declares "schema is at head", this makes
        # the DATA match that claim.
        _dm = run_data_migrations(args.target)
        if _dm["failed"]:
            print(f"{BAD} aborting — a data step failed, so the target holds a "
                  f"correct schema over uncorrected data")
            return 1
        normalize_phones(args.target)
        print()

    print("[4] Verification battery")
    ok = verify(args.source, args.target, strict=args.strict)

    print("\n[5] Manual follow-ups (not scripted — see README):")
    print("    · psql -f scripts/create_ai_readonly_role.sql   (gi_ai_ro for the AI layer)")
    print("    · VACUUM ANALYZE;                               (fresh planner stats)")
    print("    · verify deploy/.env secrets on the server (JWT_SECRET, WHATSAPP_*, SMTP_*)")

    print(f"\n== CUTOVER: {'✅ VERIFIED' if ok else '❌ FAILED — do not go live'} ==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
