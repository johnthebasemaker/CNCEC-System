# `tools/pg_excel_sync.py` — Excel → PostgreSQL sync (run log)

**Date:** 2026-07-28 · **Branch:** `feat/pg-excel-sync` (off `main` at `1949884`)
**Gates:** service_tests **853 → 880 (+27)** · legacy `bug_check.py` **599 / 0** ·
`gi_database.db` sha256 **unchanged** (`00652932…ba038` before and after)

---

## 1. What was built

A single-command, idempotent, atomic sync from the four root workbooks into
PostgreSQL. Dry-run by default; `--commit` is required to write.

```bash
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
  .venv/bin/python tools/pg_excel_sync.py --site CNCEC            # dry-run
  …                                       --site CNCEC --commit   # writes
```

| Workbook | → | Table(s) |
|---|---|---|
| `CNCEC_Inventory.xlsx` (sheet *Inventory*) | | `inventory` |
| `CNCEC_Inventory.xlsx` (Receipt/Consumption/Return Log) | | `receipts`, `consumption`, `returns` |
| `For_1_SQM.xlsx` | | `sme_recipe` |
| `Equipment.xlsx` | | `sme_equipment` + `sme_sqm_progress` |
| `Materials_DetailsAvailable_Qty.xlsx` | | `sme_inventory_seed` |

Flags: `--dir` · `--site` · `--kinds` · `--commit` / `--dry-run` · `--user` ·
`--sme-reseed` (+ `--force-drop-progress` guard, carried over unchanged).

## 2. ⚠️ This overlaps an existing tool — read before adopting

`tools/excel_sync.py` **already** reads these same four workbooks, already
targets PostgreSQL, already defaults to dry-run, and already requires
`--commit`. The brief for this task asked for a new script; that was built as
asked, but the overlap is real and should be a deliberate decision, not a
surprise. The honest summary:

* **Keep both** — `excel_sync.py` mirrors `POST /import/{kind}` exactly and is
  what the 2026-07-13/18 injection history was performed with. It is the
  reference implementation.
* **`pg_excel_sync.py` is the operational entry point** — it collapses the
  three-command chain in `tools/migration/README.md` into one, and adds the
  three properties below.
* **Do not fork the mapping logic.** Both scripts import the planners from
  `backend/api/bulk_import.py`. That module stays the single source of truth
  for column mapping. Nothing was re-derived in pandas — see §4.

If you would rather have one script, the right move is to retire
`excel_sync.py` and re-point the cutover runbook, not to let the two drift.

## 3. What the new tool adds over `excel_sync.py`

### 3.1 Atomicity (the biggest one)

`excel_sync.py` commits **per kind**. A failure on the last workbook leaves
inventory and the ledger written and the SME masters not — a half-synced
database with no signal that it is half-synced. `pg_excel_sync.py` runs all
five kinds in **one transaction** and commits once.

Verified by deliberately breaking the *last* workbook and re-running:

| Write path | rows leaked after a failure in the last kind |
|---|---|
| per-kind commit (old behaviour) | `(1, 1, 1, 1)` — inventory, ledger, recipe, equipment all persisted |
| single transaction (this tool) | `(0, 0, 0, 0)` |

A side benefit: the `pending_saps` shim disappears on the commit path. Because
inventory rows are `flush()`ed inside the same transaction, the ledger's
soft-FK check sees brand-new SAPs naturally instead of being handed a set.

### 3.2 PostgreSQL-native upserts

Every master-data write is `INSERT … ON CONFLICT (<natural key>) DO UPDATE`.
Conflict targets are asserted against `backend/models.py`:

| Kind | Conflict target | Backed by |
|---|---|---|
| `inventory` | `SAP_Code` | PK |
| `sme-equipment` | `Site_ID, Equipment_Tag_No, Lining_System_Code` | UNIQUE |
| `sme-recipes` | `Lining_System_Code, Material_Code, SAP_Code` | UNIQUE |
| `sme-materials` | `Material_Code` | PK |

The SET clause is `COALESCE(excluded.col, table.col)`, not a bare
`excluded.col`: a column the workbook leaves blank must never null out a value
already stored.

This matters in practice — the API and the dev servers were **live** during
this work (uvicorn :8000 + two Vite servers, with a user editing SME master
data through the UI). `apply_*` in `bulk_import.py` uses a plain `insert()`,
which dies with a duplicate-key `IntegrityError` if a row appears between plan
and apply. ON CONFLICT converges instead.

### 3.3 PostgreSQL-only, `gi_database.db` unreachable

`assert_postgres_target()` refuses a SQLite URL, a bare `.db` path, a
non-Postgres dialect, and even a Postgres URL that names `gi_database`. The
check is **scheme-first** on purpose: an early substring hunt for `".db"`
would have falsely refused a legitimate URL whose *host* contains it
(`db.example.com`, `x.dbhost.net`). That false-refusal bug was caught and fixed
before commit. The target URL is echoed with its password redacted.

## 4. Two deliberate deviations from the brief

**pandas was not used.** The brief allowed "pandas (or the project's existing
Excel library)". pandas is installed here only *transitively* via streamlit for
the legacy app — it is **not** in `backend/requirements.txt`, only `openpyxl`
is. A production-path tool that imports an undeclared dependency breaks the
first time the deploy image is built without streamlit. Reading is openpyxl,
via `bulk_import`.

**The column mapping was not re-implemented.** The brief said to map columns to
the models; `bulk_import.py` already does exactly that, and it encodes rules
that took several passes to get right — Material_Code re-mapping and release,
the ledger's three-tier (day, SAP, qty, ref) reconcile, category
canonicalisation that keeps the MTC hard-block armed, Name-identity tag
backfill, `Done_SQM` preservation. Re-deriving those in pandas would create a
second set of rules that disagrees with the API's about the same spreadsheet.
Only the **write path** is new.

## 5. One ordering bug fixed along the way

`excel_sync.py` loads `sme-equipment` **before** `sme-recipes`.
`plan_sme_equipment` backfills a missing `Lining_System_Code` from
`Lining_System_Short_Name` by looking it up in `sme_recipe` — so on a fresh
database that map is empty and short-name-only rows are silently skipped. The
current workbooks populate the code directly, so this has never bitten, but the
new tool loads **recipes → equipment** and suite AW asserts the order.

## 6. Idempotency — where it holds, and where it cannot

* **Master data** — genuinely converges via ON CONFLICT. Verified: a second
  `--commit` is a no-op (`+0 new ~0 changed`, row counts unchanged).
* **Ledger** — `receipts`/`consumption`/`returns` have **no unique
  constraint**; their PK is a surrogate `id` and the same (date, SAP, qty) line
  is a legitimate repeat movement. `ON CONFLICT` is impossible there and is
  **not faked**. Idempotency comes from `plan_ledger`'s reconcile, which
  consumes exact matches before proposing an insert. Suite AW asserts those
  three tables still have no unique key, so anyone who adds one is sent back to
  the docstring first.
* **Known gap, reported at runtime** — `sme_recipe`'s natural key includes
  `SAP_Code`, and Postgres treats NULLs as distinct in a UNIQUE constraint, so
  a recipe line without a SAP cannot be caught by ON CONFLICT. The plan still
  classifies it correctly (it keys on `sap or ""`), so this is a lost safety
  net rather than a correctness bug. The run prints how many such lines it saw.

## 7. Test evidence — suite AW (+27 checks)

Added to `backend/api/service_tests.py`. Hermetic: workbooks are built in
memory under `SVCW-`/`SVCX-`/`SVCY-` keys, written to a temp dir, and every
committed row is deleted at the end (audit rows stay — append-only contract).

* **Guards ×7** — SQLite / bare `.db` / `gi_database` / non-Postgres refused;
  a real Postgres URL and a `.db`-containing *host* both accepted; password
  redacted.
* **Structural ×7** — each ON CONFLICT target matches a real PK/UNIQUE in the
  metadata; the three ledger tables still have none.
* **Ordering ×2** — inventory before ledger, recipes before equipment.
* **Upsert semantics ×3** — re-upserting an existing key raises nothing;
  DO UPDATE applies supplied columns; a blank column is preserved.
* **End-to-end CLI ×7** — dry-run is the default and writes nothing; `--commit`
  applies all five kinds; stock reconciles `1/1`; the SQM baseline is seeded;
  a second `--commit` is a no-op; `--commit --dry-run` is refused (exit 2).
* **Atomicity ×1** — a broken last workbook rolls back every earlier kind.

### Revert-verification (the tests were proven to have teeth)

Each claim was re-run against deliberately sabotaged code:

| Sabotage | Result |
|---|---|
| plain `INSERT` instead of ON CONFLICT | ❌ 2 checks fail — `UniqueViolationError` on `inventory_pkey` |
| bare `excluded.col` instead of `COALESCE` | ❌ 1 check fails — stored `Minimum_Qty` blanked to `None` |
| `commit()` per kind instead of one transaction | ❌ 1 check fails — `leaked=(1,1,1,1)` |

The COALESCE check **passed vacuously on its first draft** (the column was
merely *absent* from the payload, so it never reached the SET clause at all).
It was rewritten to send the column explicitly as `None` — which is the case
COALESCE actually exists for — and only then did the sabotage fail it.

## 8. Live-run result against the mirror

```
== pg_excel_sync (DRY-RUN) ==
   target : postgresql+asyncpg://postgres@127.0.0.1:5433/gihub
▶ inventory       +0 new  ~0 changed  =429 unchanged  rejected=0
▶ ledger          receipts +0/=488 · consumption +0/=419 · returns +0/=9
▶ sme-recipes     +0 new  ~0 changed  =41 unchanged
▶ sme-equipment   +0 new  ~0 changed  =85 unchanged
▶ sme-materials   +0 new  ~1 changed  =21 unchanged
== STOCK VERIFICATION: 429/429 SAPs match the workbook's Current Stock ==
```

`429/429` matches the documented production oracle in
`tools/migration/README.md`. **Nothing was committed to the mirror** — every
run against the real workbooks was a dry-run.

**One genuine drift found:** `sme_inventory_seed.GI-6002241` has
`Initial_Ordered_Qty = 840.0` in the database and `840.4` in the workbook.
Operator decision whether to apply it.

## 9. Note on concurrent use

The API and dev servers were running throughout, and SME master data was being
edited through the UI during this work (visible as `updated_at` changes on
`sme_inventory_seed`). Dry-run output is therefore expected to shift between
runs while the live app is in use. The ON CONFLICT write path is designed for
exactly this, but for a *cutover* run the app should still be quiesced so the
stock-verification line is meaningful.
