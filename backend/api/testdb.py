"""
backend/api/testdb.py — the throwaway database `service_tests` runs against.

WHY THIS EXISTS
---------------
`service_tests` is not a unit suite. Suite A calls the write services inside a
transaction and rolls back, so it leaves nothing behind — but suites B…BV drive
the REAL ASGI app over httpx, and an HTTP request that comes back 201 has
already committed by the time the assertion reads it. There is no rollback to
hold: the commit is the thing being tested.

So every run wrote to whatever database `DATABASE_URL` named, and the local
default named the operator's live one. A full pass left behind audit rows, mock
PRs, notifications, outbox entries, test users, vendors and employees, mixed in
among real stock movements. The suites do clean up after themselves — and it
does not help, because a run that fails early, is interrupted, or dies on an
un-caught exception skips its own `finally`, and those are exactly the runs a
developer does most often.

Cleanup is the wrong shape for this problem. The fix is that the tests never
open the live database at all.

THE TWO PROPERTIES
------------------
ISOLATED
    The live database is not cleaned up after — it is never connected to.
    `provision()` rewrites `DATABASE_URL` before `backend.api.db` is imported,
    which is the only moment that matters: the engine is built at import time
    from whatever the URL says then. The guard below REFUSES to run when the
    source and target name the same database, so the failure mode is a loud
    abort rather than a silent write to production data.

REPRODUCIBLE
    The test database is rebuilt from `gi_database.db` by the same production
    cutover script the Playwright harness uses. ⚠️ That file is NOT in git —
    it was untracked on 2026-07-26 (commit a09da0b) because it holds real
    employee names and stock, so this suite only runs where a snapshot exists.
    CI generates a fixture instead (tools/make_ci_fixture_db.py), which is
    enough for dual_ci/parity_check but NOT for this suite; see
    docs/PROJECT_STATUS.md 1b.

    The sentence that used to stand here claimed the file was in git and
    "itself a gate, so every machine and CI start from identical rows". That
    was the intent and never the fact, and a promise the repository cannot keep
    is worse than an admitted limitation.

    This half is not a nicety. Before it, the suite had quietly come to depend
    on rows that existed only on the operator's box — a database wipe on
    2026-08-13 turned 1474/0 into a hard `IndexError` on the FIRST suite,
    because employee `30001` had been sitting in the live `employees` table
    since June and nothing recreated it. A test that passes because of what
    somebody's laptop happens to contain is not a gate.

WHAT IT COSTS
-------------
About a second. The workbook is 1.2 MB, so the rebuild is far cheaper than the
suite it protects, and it runs unconditionally rather than being something to
remember.

ESCAPE HATCH
------------
`GI_TEST_DB=off` runs in place against `DATABASE_URL` (the pre-2026-08-13
behaviour). It prints a warning and is meant for debugging a failure that only
reproduces against live data — never for a normal run.
`GI_TEST_DB_REUSE=1` skips the rebuild when the database already has tables,
for tight iteration on one suite. Both are opt-in and both are stated out loud.
"""
from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CUTOVER = _ROOT / "tools" / "migration" / "cutover_migrate.py"
# ⚠️ HONOURS `GI_DB_FILE`, and must, because this path is only used to say
# "the source is missing" BEFORE shelling out to cutover_migrate — if the
# two disagree about which file is the source, this check guards a file
# nobody reads and the real failure arrives later and less clearly. CI has
# no `gi_database.db` at all (gitignored); it points this at a generated
# fixture. See tools/make_ci_fixture_db.py.
_SOURCE_DB = Path(os.environ.get("GI_DB_FILE")
                  or (_ROOT / "gi_database.db"))

DEFAULT_TEST_DB = "gihub_svctest"


def _split_url(url: str) -> tuple[str, str]:
    """Split a SQLAlchemy Postgres URL into (prefix-up-to-and-including-'/', dbname).

    Deliberately string-level rather than urlsplit(): these URLs carry a
    `postgresql+driver://` scheme that urlsplit does not special-case, and the
    only edit needed is the last path segment.
    """
    base, _, db = url.rpartition("/")
    return base + "/", db


def _sync(url: str) -> str:
    """psycopg2 form — cutover_migrate and psql speak sync, the app speaks async."""
    for driver in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if url.startswith(driver):
            return "postgresql+psycopg2://" + url[len(driver):]
    return url


@contextmanager
def _connect(url: str):
    """A raw psycopg2 connection in AUTOCOMMIT.

    ⚠️ This yields the CURSOR, and the connection is closed in a `finally`
    rather than by psycopg2's own `with connection:` — because that context
    manager opens an explicit transaction block even when autocommit is on
    (verified: `transaction_status` goes to INTRANS after the first statement).
    `CREATE DATABASE` cannot run inside one, so `with psycopg2.connect(...)`
    is precisely the thing that must not be used here.
    """
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    dsn = url.split("://", 1)[1]
    conn = psycopg2.connect(f"postgresql://{dsn}")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()


def provision() -> str | None:
    """Build (or reuse) the throwaway database and point DATABASE_URL at it.

    MUST be called before `backend.api.db` is imported. Returns the resolved
    sync URL, or None when isolation is switched off.
    """
    if os.environ.get("GI_TEST_DB", "").strip().lower() in ("off", "0", "no", "false"):
        live = os.environ.get("DATABASE_URL", "(config default)")
        print(f"⚠️  GI_TEST_DB=off — running IN PLACE against {live}.\n"
              f"    This run WILL leave test rows behind. Unset GI_TEST_DB to isolate.")
        return None

    # The source is only ever read for its host/port/user; its DATABASE is not
    # opened. Falling back to the config default keeps the bare
    # `python -m backend.api.service_tests` invocation working.
    from .config import async_database_url
    source = _sync(os.environ.get("DATABASE_URL", "").strip() or async_database_url())
    base, source_db = _split_url(source)
    target_db = os.environ.get("GI_TEST_DB", "").strip() or DEFAULT_TEST_DB

    # THE GUARD. Everything above is convenience; this is the property. A
    # mis-set GI_TEST_DB must not quietly resolve back onto the live database —
    # that is the exact accident this module exists to make impossible.
    if target_db == source_db:
        print(f"❌ REFUSING TO RUN — the test database and the source database are "
              f"both {target_db!r}.\n"
              f"   service_tests commits through the real ASGI app, so this would "
              f"write test rows into the database the app is using.\n"
              f"   Set GI_TEST_DB to a different name, or GI_TEST_DB=off to accept "
              f"that deliberately.")
        sys.exit(2)

    target = base + target_db
    admin = base + "postgres"

    exists = _ensure_database(admin, target_db)
    reuse = os.environ.get("GI_TEST_DB_REUSE", "").strip() in ("1", "true", "yes")
    if exists and reuse and _has_tables(target):
        print(f"[testdb] reusing {target_db} (GI_TEST_DB_REUSE=1 — NOT rebuilt)")
    else:
        _load(target)

    _apply_fixtures(target, target_db)
    os.environ["DATABASE_URL"] = target

    # The AI lane connects as a DIFFERENT ROLE to the same cluster, so it has
    # its own URL carrying its own credentials. Redirect only its DATABASE and
    # leave the credentials alone: if it kept pointing at the live database
    # while everything else wrote the throwaway, the suite would still pass —
    # it would just be proving the wall on a database nothing under test had
    # touched. Redirecting here rather than in CI keeps the test database's
    # name in exactly one place.
    ro = os.environ.get("GI_AI_RO_URL", "").strip()
    if ro:
        os.environ["GI_AI_RO_URL"] = _split_url(ro)[0] + target_db

    print(f"[testdb] isolated: {target_db}  ·  live {source_db!r} untouched")
    return target


def _ensure_database(admin_url: str, name: str) -> bool:
    """Create the database if absent. Returns True if it already existed."""
    with _connect(admin_url) as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        if cur.fetchone():
            return True
        # Identifiers cannot be parameterised; `name` comes from GI_TEST_DB
        # or the constant above, so restrict it to a safe shape rather than
        # interpolating whatever the environment holds.
        if not name.replace("_", "").isalnum():
            raise SystemExit(f"❌ GI_TEST_DB={name!r} is not a plain identifier")
        cur.execute(f'CREATE DATABASE "{name}"')
    return False


def _has_tables(url: str) -> bool:
    with _connect(url) as cur:
        cur.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r'")
        return cur.fetchone()[0] > 0


def _load(target: str) -> None:
    """Reload from gi_database.db via the production cutover script.

    Reusing cutover_migrate rather than hand-rolling a loader is deliberate:
    it is the script that will build the production database, so the suite now
    runs against a schema produced exactly the way production's will be. That
    is how the missing `ux_asset_transfer_open` partial index was found — it
    lived only in an alembic revision, so it was present on every migrated
    database and absent from every created-from-models one.
    """
    if not _SOURCE_DB.is_file():
        raise SystemExit(f"❌ {_SOURCE_DB} is missing — it is the test-data source")
    proc = subprocess.run(
        [sys.executable, str(_CUTOVER), "--wipe", "--target", target],
        cwd=str(_ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"❌ could not build the test database (exit {proc.returncode})")


def _apply_fixtures(target: str, dbname: str) -> None:
    """State the live database has and a cutover-built one does not.

    Each of these was a real suite failure, and each is here rather than in the
    suite because it belongs to the DATABASE, not to a test. They are worth
    reading as a list, because together they say something about
    `cutover_migrate.py`: it copies the legacy rows and STAMPS alembic to head
    without running it, so every migration that backfilled DATA is skipped on a
    freshly cut-over database. The schema is right; the data corrections are
    not replayed.
    """
    with _connect(target) as cur:
        # (1) The AI lane's read-only role. Roles are cluster-wide but GRANTs
        # are per-database, so a new database always needs this — which is why
        # the project already has a "re-run it after every reload" ritual.
        # Doing it here retires the ritual for tests.
        sql = (_ROOT / "backend" / "scripts" / "create_ai_readonly_role.sql").read_text()
        # The script hard-codes `GRANT CONNECT ON DATABASE gihub`.
        cur.execute(sql.replace("ON DATABASE gihub ", f'ON DATABASE "{dbname}" '))

        # (2) alembic d2f84b19e57c backfilled employee 30816's site. It is a
        # DATA migration, so a cut-over database never gets it — and a
        # site-less employee is invisible to every supervisor request, because
        # create_smr tests (site or '') != site_id and no site satisfies that.
        # Suite BP asserts precisely this.
        cur.execute("""UPDATE employees SET "Site_ID" = 'CNCEC'
                        WHERE "ID_Number" = '30816'
                          AND COALESCE("Site_ID", '') = ''""")

        # (3) The operator re-categorised nine safety items to 'PPE' in the
        # master workbook AFTER the cutover snapshot was taken, so the frozen
        # data still calls them 'Safety'. Suite BO needs the category to exist
        # at all. These are the exact nine SAPs that carry it in the live
        # master — not an invented fixture.
        cur.execute("""UPDATE inventory SET "Category" = 'PPE'
                        WHERE "SAP_Code" IN ('1223','1226','1229','1246',
                                             '1262','1263','1264','1265','1266')""")

        # (4) The entry-document gate defaults ON in production. The functional
        # suites post entries without attachments; the gate has its own suite
        # (AH) that turns it on for itself. Same line the Playwright harness
        # runs, for the same reason.
        cur.execute("""INSERT INTO app_settings (key, value)
                       VALUES ('require_entry_documents', '0')
                       ON CONFLICT (key) DO UPDATE SET value = '0'""")
