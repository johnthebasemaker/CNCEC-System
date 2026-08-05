# Excel → Location Sync + Material-Name UI — run log

**Branch** `feat/excel-location-sync-and-ui` · **Date** 2026-08-05
**Baseline** `84c9927` (main, after PR #29)

---

## Read this first: the two new columns are empty

You asked me to wire the sync to the `Rack/Current Location` and
`Location` / `Serial No.` columns. Both columns exist in
`CNCEC_Inventory.xlsx` and I read them before writing any code. **They carry
almost no data yet:**

| Column | Sheet | Populated |
|---|---|---|
| `Rack/Current Location` | Inventory | **0 of 453 rows** |
| `Location` | Consumption Log | **1 of 1,166 rows** — `"At site"` |
| `Serial No.` | Consumption Log | present, but the one located row is **blank** |
| `Status` | Consumption Log | **column does not exist** |

So a sync today seeds **nothing**, and that is the correct outcome rather than
a failure. The code is built, tested against synthetic workbooks that DO carry
the data, and will seed the moment you start filling the cells. See
[What to type in the spreadsheet](#what-to-type-in-the-spreadsheet).

The one row that has a Location has no serial, so it cannot be keyed. The sync
names it back to you every run instead of inventing a serial for it:

```
⚠ 1 row(s) have a Location but no Serial No. — cannot be keyed, so no asset
  was created: row 9 (SAP 1169, 'At site')
```

---

## Phase 1 — a code is not a name

`sme_consumption_log` stores `Material_Code` and nothing else, so the Actual-draw
queue asked an operator to recognise `GI-6000012` on sight while deciding which
tank a drum went on.

**Where the name comes from matters.** Both `sme_inventory_seed` and
`sme_recipe` carry a `Material_Name`. I took it from **`sme_recipe`**, because
rule 1a makes the seed the sole source of every SME *quantity* and
`sme_actuals.py` deliberately never names that table. Adding a label lookup into
it is exactly how a quantity read sneaks in six months later. A material with no
recipe line simply shows its code — an honest gap beats a second path into the
table rule 1a fences off.

I then audited every table in the app that renders an identifier. Most already
carried a description. These six did not:

| Screen | Was | Now |
|---|---|---|
| SME → Actual Consumption → Actual draw | `Material_Code` | code + name (wrapped, rule 5) |
| SME → Execution Plan → Production details | `Material_Code` | code + name from the client model |
| Burn Rate | `SAP_Code` only | SAP + Material + Description + UOM |
| Admin Console → Lots | `SAP_Code` | + Description |
| Warehouse → Returns from site | `Material_Code` | + Description |
| Logistics → Vendor Returns | `Material_Code` | + Description |

Backend side, that is one shared helper —
`services/ledger.attach_material_names()` — rather than a join per endpoint,
because the lists differ in *which* identifier they hold (lots carry the SAP, PO
returns carry the Material_Code) and both need the same answer. It joins on
`TRIM` like every other SAP comparison, and **LEFT** joins on purpose: a
consumption row whose SAP has since left the master is the row most worth
seeing, and an inner join would silently drop it.

---

## Phase 2 — racks from the Inventory sheet

`Rack/Current Location` → `storage_locations` + `material_locations`.

The text becomes the rack's `code` (the QR payload on the shelf label) **and**
its `description`. Zone / rack / row / bin are left empty on purpose: one free-
text column cannot be split into four without guessing, and with zero rows of
real data any parser I wrote would be unvalidated speculation. `_label()`
already falls back to the description, so an unparsed rack reads correctly in
the locator today, and you can fill the breakdown in the app whenever you like.

Two SAPs naming the same shelf create **one** rack and two links — a rack is a
place, not a property of a material. That is why this is `material_locations`
and not a column on `inventory`.

---

## Phase 3 — assets from the Consumption Log

### The Golden Rule

> **A `Location` makes the row a reusable asset. Blank means consumable.**

Nothing else is consulted — not the category, not the SAP prefix, not whether a
serial happens to be present. That single test is what separates a hammer from
a drum of primer, and it is your convention rather than one I invented. It also
matters that it is the *only* test: 1,165 of the 1,166 real rows are blank, so a
planner keying off anything looser would manufacture a thousand phantom hammers
on its first run.

Units are keyed `(Site_ID, SAP_Code, serial_no)` — the constraint already on the
table. Two hammers share a SAP and are told apart by serial; that is the whole
reason `asset_units` is not a column on `inventory`.

### Status

The workbook has **no Status column**, so the app is where condition gets
recorded. I widened the vocabulary to yours:

| Group | Values |
|---|---|
| **Condition** | `working` · `not_in_use` · `repair` |
| **Custody** | `in_stock` · `issued` · `returned` · `lost` · `scrapped` |

One field, not two — you asked for a single status and in practice people record
whichever fact they have. A second column would be empty on every row somebody
did not think to fill, and `asset_movements.status` already gives the history of
how it changed. The condition values come first in the picker because that is
what somebody standing in front of the hammer actually knows.

If you *do* add a `Status` column later, the sync maps prose onto those values
(`Working`, `in use`, `not in use`, `idle`, `Under Repair`, `maintenance`,
`damaged`, `lost`, `scrapped`, …). An unrecognised value is **not** invented —
the unit is created with the safe default and the value is named back to you.

### App Wins

A spreadsheet cell is a starting point typed by whoever last edited the file. A
row a store keeper wrote after walking to the thing and scanning it — with a GPS
fix attached — is the truth. So:

* an existing unit keeps its **status**, its **rack** and above all its
  **`current_lat` / `current_lng`**; the sync proposes no change to any of them;
* `storage_locations` upserts `DO NOTHING`, so your zone/rack/row breakdown
  survives;
* a SAP that already has *any* rack assignment is left alone entirely.

There is exactly one narrow exception, and it is deliberate: a unit **the app
has never touched** does take a corrected Location text. The guard is
`last_seen_by = 'excel-sync'` **and** no coordinates recorded — every app path
stamps `last_seen_by` with the real username, so the predicate is false the
moment a human is involved. Refusing here would strand a typo for no benefit.

This is tested the only way that means anything: write through the API first,
re-run the sync, read back.

---

## What to type in the spreadsheet

**To locate a material** — Inventory sheet, `Rack/Current Location`:

```
A-01-2
```

Anything readable works; it becomes the shelf's code and its label. Two rows
with the same text share one shelf. Leading/trailing spaces are collapsed, so
`" A-01-2 "` and `"A-01-2"` are the same place.

**To track an asset** — Consumption Log, fill **both**:

| `Serial No.` | `Location` |
|---|---|
| `HMR-0041` | `Yard bay 4` |
| `HMR-0042` | `Truck 4771` |

Same SAP, two serials → two tracked hammers. Leave `Location` blank and the row
stays ordinary consumption, exactly as today.

Then run the sync and set each unit's condition on **Assets → Move**.

---

## Using the GPS scanner

Unchanged from the overnight build, restated because it is the point of the
feature: Assets → **Scan**, point at the label, and the unit resolves — by
serial if the label carries one, or into a "which one is in your hand?" choice
if the sticker only has the SAP. **Move** captures coordinates alongside the
update, never in front of it: a declined permission or a warehouse with no
signal still records the move, with the coordinates null. Capture needs HTTPS
(or localhost) — `navigator.geolocation` is a secure-context API.

---

## Gates

| Gate | Result |
|---|---|
| Backend service tests | **1228 / 0** (was 1201; +27 in suite BJ) |
| Playwright E2E | **57 / 57** |
| SME TS ↔ PY parity | **1313 comparisons, PASS** |
| SME UI math | **33 / 0** |
| Legacy `bug_check.py` | **599 / 0** |
| Frontend build | clean |
| Alembic | single head `a71e93b4c2f8` — **no migration needed** |
| `tools/parity_check.py` | ❌ — **pre-existing, see below** |

No migration was required: `storage_locations`, `material_locations`,
`asset_units` and `asset_movements` all landed in the overnight build, and
`asset_units.status` is free text, so widening the vocabulary is a validator
change only.

### Two test failures that were not caused by this work

Both were verified by stashing these changes and re-running on `main`.

**1. `bg: the lookup uses ix_material_locations_sap`** — my own overnight test
asserted a *planner choice*. On a table with three rows a Seq Scan **is** the
cheaper plan and picking it is correct, so the assertion failed whenever the
fixture was the only data. Rewritten to assert what the design actually claims:
with the sequential path closed, the planner reaches for that index — i.e. the
index covers the predicate — plus a real timing bound on the live table. Fixed.

**2. `tools/parity_check.py` — all 5 views** — this compares the frozen legacy
SQLite against Postgres. They have diverged permanently:

| | SQLite | Postgres |
|---|---|---|
| `inventory` | 306 | 466 |
| `consumption` | **1** | 1,133 |
| `receipts` | 70 | 575 |

`gi_database.db` was frozen at cutover; every sync since has written Postgres
only. **This gate cannot pass again** and its failure carries no information
about any code change. I have left it untouched — retiring or re-baselining it
is your call, not mine.

---

## The live sync

Your exact command, run twice:

```bash
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub .venv/bin/python tools/pg_excel_sync.py --site CNCEC --erp --commit
```

**First run** — `inventory +1`, `ledger +53`, `sme_logged 3`,
`racks 0 · links 0 · units 0` (both columns empty), one atomic transaction.
**Second run** — every counter `0`. Idempotent.

Stock verification **450 / 453**. The three that disagree are pre-existing data
problems, not sync faults, and two of them are impossible states worth a look:

```
1137: workbook=0.0    db=-15.0
1190: workbook=142.0  db=134.0
1358: workbook=0.0    db=-2.0
```

Negative stock means more was issued than ever arrived — either a missing
receipt or a double-counted consumption.

No test data reached the mirror: suite BJ uses `SVCBJ-` keys throughout and
deletes every row it writes.
