# PROPOSED ARCHITECTURE PLAN — Asset Tracking, Rack Locator, SME Polish

> **Status: PLANNING ONLY. No application code has been written.**
> Written 2026-08-04 against `main` @ `313ee57`, branch
> `fix/e2e-flake-and-handover-sync`. Every number below was measured against
> the live workbook and the live `:5433/gihub` mirror, not assumed.
>
> Read alongside [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md) (the authority on
> the 14 locked rules) and [`SESSION_HANDOVER.md`](SESSION_HANDOVER.md).

---

## 0. Executive summary — what the analysis changed

Five things I found before designing anything. Each one changes the shape of
the work, and three of them mean **less** code than the brief assumes.

| # | Finding | Consequence |
|---|---|---|
| **A** | **The extra sheets are already ignored.** `_sheet_rows()` selects worksheets **by name** ([bulk_import.py:118](backend/api/bulk_import.py:118)). A live `--erp --dry-run` against the new 14-sheet workbook parsed the 4 sheets it wanted and never opened the other 10. | Phase 1 is *hardening*, not a rewrite. The real gap is 3 unmapped columns and one unsafe fallback. |
| **B** | **`sme_consumption_log` already exists and is empty.** 20 columns, `Equipment_Tag_No` + `Lining_System_Code` + `Material_Code` + `Expected_Qty`/`Actual_Qty`, **0 rows**, read only by a variance report ([sme.py:269](backend/api/sme.py:269)) and Man-Hours. **Nothing in either engine reads it.** | The Surface-Shield routing has a decoupled home *by construction*. Rule 1a is preserved without inventing a table. |
| **C** | **`Tank No.` cannot be auto-matched — it is ambiguous, not just dirty.** | This is the one place the brief cannot be delivered as literally stated. See §2.3. It needs an operator-owned alias table. |
| **D** | **The new Location / Serial columns are empty or mean something else.** `Inventory.Current Location` is blank in **452 of 452** rows. `Consumption Log.Location` has **1** non-null value. `Receipt Log.Location` is the same site string 561×. `Consumption Log.Serial No.` is a **batch/lot number** (`3441` on a primer can), not an asset serial. | The Excel **cannot seed** locations or asset identity. The app must be the source of truth, and the sync must not pretend otherwise. |
| **E** | **The Auditor is already allowed through the API.** `/hod/*` and `/sme/*` are `Depends(require_level(2))`; `auditor` is **level 3**. The block is *only* `anyRole: ['hod']` in the frontend nav manifest. | Item 5.1 is a 6-line frontend change with **no backend edit** — and the write guard still holds. |

**Ambiguity I am flagging rather than guessing:** the brief says route Surface
Shields "as a consumption … don't want to affect the plan". Those two clauses
pull in opposite directions unless the consumption lands somewhere the engine
never reads. §2.4 is my reading; if you meant it to *decrement* the SME
availability, that is a deliberate overturn of **rule 1a** and I will not do it
without you saying so explicitly.

---

## 1. What the new workbook actually contains

`CNCEC_Inventory.xlsx`, 367 KB, modified 2026-08-04 20:29. **14 sheets**, of
which the sync consumes **4**.

| Sheet | Rows | Status |
|---|---|---|
| `Inventory` | 452 | **consumed** → `inventory` master |
| `Consumption Log` | 1,110 | **consumed** → `consumption` ledger |
| `Receipt Log` | 565 | **consumed** → `receipts` ledger |
| `Return Log` | 10 | **consumed** → `returns` ledger |
| `Request` | 26 | ignored |
| `MASTER EQUIPMENTS` | 104 | ignored (53 carry a `Tag #`) |
| `CUMI MATERIAL RECEVIED ` | 31 | ignored (note the trailing space + typo) |
| `⚙ VBA Setup Guide` | 335 | ignored |
| `Safety Items` | 49 | ignored |
| `RL CONSUMABLES` | 68 | ignored |
| `RL TOOLS AND TACKLES` | 61 | ignored |
| `BR CC PU  TOOLS AND TACKLES` | 56 | ignored (note the double space) |
| `ELECTRICAL ITEMS` | 12 | ignored |
| `INSTRUMENTS` | 83 | ignored |

### 1.1 New columns, and what is actually in them

```
Inventory         + Current Location      452/452 BLANK
                  + Audit 13/06/26        (a dated stock-count column)
Consumption Log   + Location              1 non-null value ("At site")
                  + Current Stock         a spreadsheet formula result
                  + type                  1,110/1,110 POPULATED  ← the routing key
Receipt Log         Serial No.            79 real values (rest "N/A")
                    Location              561× "CNCEC - RAS AL KAHIR" (a SITE)
```

`type` distribution — this is the signal the brief is asking for:

```
R/L Consumables 541 · BR CC PU Tools 292 · Surface Shield 103 · Safety 76
R/L Tools 29 · Electrical Items 44 · EQUIPMENTS/TOOLS 15 · Others 7
Blasting 1 · Office 1 · QC 1
```

The `Inventory` sheet carries the same taxonomy in `Category`, and
`CATEGORY_CANON` ([bulk_import.py:74](backend/api/bulk_import.py:74)) already
folds `"surface shield" → "Surface Shields"` — the dry-run reported
`category canonicalised: Surface Shield → Surface Shields ×35`.

### 1.2 Live dry-run evidence

```
DATABASE_URL=… tools/pg_excel_sync.py --site CNCEC --erp --dry-run
```

```
▶ ledger  (CNCEC_Inventory.xlsx)
      receipts     +70 new  ~2 corrected  =486 matched
      consumption  +713 new ~0 corrected  =397 matched
      returns      +1 new   ~0 corrected  =9 matched
      ⚠ Consumption Log: ignored unmapped column(s): Location, Current Stock, type
      ✗ Receipt Log rows 559/560/561 — missing/unparseable Date or Qty
== STOCK VERIFICATION: 321/452 SAPs match the workbook's Current Stock ==
```

Two things to read off that:

* the **only** new-column complaint is the Consumption Log's three;
* **131 of 452 SAPs disagree** on Current Stock. That is pre-existing drift
  between the workbook and the ERP ledger, not something this work introduces,
  but it means **the workbook is not a safe source of truth for stock** — which
  is another reason locations must live in the DB, not the sheet.

---

## 2. Phase 1 — Excel sync: sheet filtering + Surface-Shield routing

### 2.1 Harden the sheet allowlist *(small, real)*

`plan_inventory` falls back to `_sheet_rows(data, None, …)` → **`worksheets[0]`**
when the named sheet is missing ([bulk_import.py:166](backend/api/bulk_import.py:166)).
Today sheet 0 happens to be `Inventory`, so it is harmless. It stops being
harmless the moment somebody reorders tabs or renames one — the importer would
then read `Request` or `Safety Items` as the inventory master and, because
every write is `ON CONFLICT DO UPDATE`, quietly rewrite the master from the
wrong sheet.

**Change:** the single-sheet fallback keeps working for genuine single-sheet
master files, but is refused when the workbook has a sheet whose name matches
any *known* log sheet — i.e. "this is the multi-sheet CNCEC workbook and the
`Inventory` tab is missing" becomes a **422 with the sheet list**, not a silent
guess.

Add an explicit `_CONSUMED_SHEETS` constant naming the four, so the ignore
behaviour is *declared* rather than emergent, and report the ignored sheets as
one informational line (`ignored 10 sheet(s) not part of the sync: …`).

### 2.2 Map the three new Consumption Log columns

| Column | Decision | Why |
|---|---|---|
| `type` | **Map** → new `consumption.Item_Type` | It is the routing key. It must be persisted, not inferred at read time, or the routing changes retroactively when a material is recategorised. |
| `Location` | **Add to `ignore`** | 1 non-null value in 1,110 rows. Mapping it implies a data quality that does not exist. Asset location comes from §3, not from here. |
| `Current Stock` | **Add to `ignore`** | A spreadsheet formula result; `Opening_Stock + Σledger` is already computed server-side. Importing it would create a second, divergent truth. |

Both `ignore` entries carry a one-line comment naming the measurement, exactly
as the existing `cons. paper no. / pallet no.` entries do.

### 2.3 ⚠️ `Tank No.` — the blocker, and the only honest fix

The brief asks to "assign them to the Equipment (`Tank No.`)". **The column
cannot be matched automatically.** Measured over the 103 Surface-Shield rows:

```
TNK-091  39   J0091  25   J-0091  22   J091  7   J 0091  4
J092      2   Sample Plate 1   others 1
```

Five of those eight are near-certainly one tank. Normalising whitespace and
hyphens collapses `J091 / J0091 / J-0091 / J 0091` → `J091`, which **does** exist
in `sme_equipment` (6 rows, different `Lining_System_Code`s). Fine.

`TNK-091` is the problem. `sme_equipment` contains **both**:

```
522-8J10-TNK-091   TRAIN J   system 1 → 247 m² · system 5 → 89.32 m²
522-8k10-TNK-091   TRAIN K   system 1 → 247 m² · system 5 → 40.72 m²
```

A suffix match hits **two different pieces of equipment on two different
trains**, and 39 consumption rows — the largest single bucket — ride on that
choice. Guessing here would put real consumption against the wrong train and
would look completely plausible in every report.

**Design:** a new operator-owned alias table, `sme_tank_alias`.

```sql
CREATE TABLE sme_tank_alias (
  id                 serial PRIMARY KEY,
  "Site_ID"          text NOT NULL,
  alias_raw          text NOT NULL,           -- 'TNK-091', exactly as typed
  alias_norm         text NOT NULL,           -- upper, spaces/hyphens stripped
  "Equipment_Tag_No" text,                    -- NULL until an operator resolves it
  status             text NOT NULL DEFAULT 'unresolved',
                                              -- unresolved | mapped | ignored
  created_at         timestamp DEFAULT CURRENT_TIMESTAMP,
  resolved_by        text,
  resolved_at        timestamp,
  UNIQUE ("Site_ID", alias_norm)
);
```

Sync behaviour:

* every distinct `Tank No.` on a Surface-Shield row is **auto-registered** as
  `unresolved`;
* an alias whose normalised form matches **exactly one** `Equipment_Tag_No` is
  auto-`mapped` and the sync says so;
* an alias matching **zero or ≥2** tags stays `unresolved`, its rows are
  imported **with `Equipment_Tag_No = NULL`**, and the dry-run prints
  `⚠ 39 row(s) held: 'TNK-091' matches 2 equipment tags — resolve in SME →
  Tank Aliases`;
* **nothing is ever dropped and nothing is ever guessed.**

A small SME tab (`Tank Aliases`) lists unresolved aliases with a dropdown of
real tags. This is ~1 screen and it is the difference between a number you can
defend and one you cannot.

### 2.4 Routing Surface Shields into the SME portal — without touching the plan

**Route on `type = 'Surface Shield'`**, cross-checked against
`Category = 'Surface Shields'` on the inventory master. Measured overlap: 33
inventory rows are `Surface Shields`, **21 of them** carry a `Material_Code`
present in `sme_inventory_seed` (which holds 22 distinct codes / 32 component
rows). The 12 that do not are consumables outside the estimator's recipe set —
they import to the ERP ledger only, and the sync reports the count.

**Where it lands: `sme_consumption_log`,** which already exists, has **0 rows**,
and is read by exactly two things — the variance comparison
(`SQL_SME_COMPARISON`, [sme.py:269](backend/api/sme.py:269)) and a Man-Hours
rollup ([manhours.py:888](backend/api/manhours.py:888)). It is a **reporting**
table.

Rows are written with `status='committed'`, `Actual_Qty` = the workbook `Qty.`,
`Expected_Qty` = `For_1_SQM × SQM_Completed` where a recipe line exists (else
`0`), `SQM_Completed = 0` (the workbook does not state area per issue).

**How rule 1a survives — the mechanical argument, not a promise:**

1. `sme_inventory_seed` is **never written** by this path. The estimator's
   `available_qty` / `ordered_qty` come from that table and nowhere else, so no
   SME quantity can move.
2. The two SME quantity queries, `SQL_SME_MATERIALS` and `_CALC_POOL_SQL`, do
   not name `sme_consumption_log` and will not. **Suite BA already greps them**
   ([service_tests.py:9088](backend/api/service_tests.py:9088)); I extend that
   regex to include `sme_consumption_log` so a future join is caught at review.
3. A **new BA check** posts a full Surface-Shield sync and asserts every SME
   read comes back **byte-identical** — the same shape of proof the existing
   `ba:` checks use for receipts/issues/returns.

> **If you want SME consumption to reduce SME availability, say so.** It is a
> defensible thing to want. It is also a direct overturn of rule 1a, it moves
> every readiness figure on every SME tab, and it needs its own ruling, its own
> run log and a regenerated golden. I have deliberately not designed it that way.

### 2.5 SQM edit UI for a Tank/Equipment

`sme_equipment.Surface_Area_SQM` (double precision). CRUD already exists:
`backend/api/sme_master.py` → `/sme/master/*`, `require_roles("hod")`, audited,
surfaced as the 🗄️ **Master Data** tab in `SmePage`.

**Work:** add an inline-editable `Surface_Area_SQM` cell to the equipment
section of that existing tab — a `PATCH /sme/master/equipment/{id}` handler in
the module's established shape, one audit row per edit, and a confirm step
because *editing SQM changes project demand across every report*.

> ⚠️ **Conflict to decide now.** `pg_excel_sync --sme-reseed` **replaces**
> `sme_equipment` from `Equipment.xlsx` (rule 4) and would erase manual SQM
> edits. Two options: (a) reseed preserves any row whose `Surface_Area_SQM`
> differs from the workbook and reports it, or (b) manual edits are documented
> as provisional until the workbook catches up. **I recommend (a)** and will
> implement it that way unless told otherwise; it is the only one that does not
> lose operator input silently.

### 2.6 Migration + gate impact — Phase 1

```
alembic: e7c3b95a41d2 → <new>_asset_and_alias
  + consumption."Item_Type"    text NULL
  + sme_tank_alias             (table, UNIQUE (Site_ID, alias_norm))
```
Mirrored in `backend/models.py` per rule 11. `plan_ledger`'s three-tier
reconcile is unaffected — `Item_Type` is a plain nullable column and ledger
tables keep having **no** unique constraint (rule 3).

---

## 3. Phase 2 — Asset tracking, serial numbers and GPS

### 3.1 The identity problem, stated plainly

The brief's premise is that the Excel now carries serials and locations. It
does not, in any usable form:

* `Consumption Log.Serial No.` — 101 of 1,110 populated, and the values are
  **batch/lot numbers**: `3441` appears on *both* CUMIFLOOR primer components,
  `100160374` on garnet, `525106711A21425` on hardener. Consumables do not have
  unique asset serials, and these are not them.
* `Receipt Log.Serial No.` — 79 real values out of 565. Closer to a real asset
  serial, but nowhere near coverage.
* Every `Location` column is empty, single-valued, or a site name.

So the "two hammers, same SAP code" case **cannot be solved by importing the
workbook**. It needs a real asset register that the app owns.

### 3.2 `asset_units` — one row per physical thing

```sql
CREATE TABLE asset_units (
  id             serial PRIMARY KEY,
  "Site_ID"      text NOT NULL,
  "SAP_Code"     text NOT NULL,          -- FK-by-convention → inventory
  serial_no      text NOT NULL,          -- the operator's unique serial
  asset_tag      text,                   -- QR payload if distinct from serial
  status         text NOT NULL DEFAULT 'in_stock',
                                         -- in_stock | issued | returned | lost | scrapped
  current_location_id integer,           -- → storage_locations.id  (rack/shelf)
  current_lat    double precision,       -- last captured GPS
  current_lng    double precision,
  gps_accuracy_m double precision,
  location_note  text,                   -- free text for non-rack places
  last_seen_at   timestamp,
  last_seen_by   text,
  created_at     timestamp DEFAULT CURRENT_TIMESTAMP,
  UNIQUE ("Site_ID", "SAP_Code", serial_no)
);
CREATE INDEX ix_asset_units_sap_site ON asset_units ("SAP_Code", "Site_ID");
CREATE INDEX ix_asset_units_serial   ON asset_units (serial_no);
```

**`(Site_ID, SAP_Code, serial_no)` is the identity** — three hammers on SAP
`1163` are three rows. This deliberately mirrors rule 1's lesson: the thing that
distinguishes two physical objects belongs *in the key*.

**Assets only.** A row exists only where the operator creates one; consumables
never get rows, which is exactly the brief's "consumables won't have locations".
Candidate seeding: the 79 real `Receipt Log` serials, offered as a reviewable
import rather than applied blind.

### 3.3 `asset_movements` — the history, append-only

```sql
CREATE TABLE asset_movements (
  id            serial PRIMARY KEY,
  asset_unit_id integer NOT NULL,
  moved_at      timestamp DEFAULT CURRENT_TIMESTAMP,
  moved_by      text,
  from_location_id integer, to_location_id integer,
  lat double precision, lng double precision, accuracy_m double precision,
  source        text,        -- 'qr_scan' | 'manual' | 'issue' | 'return'
  note          text
);
CREATE INDEX ix_asset_movements_unit ON asset_movements (asset_unit_id, moved_at);
```

Append-only, same discipline as `system_audit_log` (never deleted). "Where has
this hammer been" is a query, not a guess. `asset_units.current_*` is a
denormalised cache of the newest movement, written in the same transaction.

### 3.4 QR scan → location

The scanner already exists and is **fully client-side**:
[`QrScanner.tsx`](frontend/src/components/QrScanner.tsx) (BarcodeDetector →
jsQR fallback) with [`lib/barcode.ts`](frontend/src/lib/barcode.ts) resolving a
decoded string to candidate identifiers — it already handles `SAP|Description`,
`SAP:1163`, URLs and small JSON.

**Extension, not replacement:** add a `SERIAL:` / `ASSET:` candidate shape to
`scanCandidates()`. Then

```
GET /assets/resolve?scan=<decoded>
```

returns, in order: an exact `asset_units` hit → **that unit's card, with its
current location**; else an inventory SAP hit with `unit_count > 1` → **a
disambiguation list of that SAP's serials**; else the existing material-card
behaviour, unchanged.

That is the "multiple hammers" requirement: the scan resolves to a SAP, the SAP
has N units, the user picks the serial — and if the sticker carries the serial,
there is nothing to pick.

### 3.5 GPS — native `navigator.geolocation`, and no map library

**Recommendation: HTML5 Geolocation only. Do not add Leaflet or Google Maps.**

* Capture is `navigator.geolocation.getCurrentPosition()` — zero dependencies,
  and it is the part that has actual value (a lat/lng on the movement row).
* Display can be a **link** — `https://www.google.com/maps?q=<lat>,<lng>` — plus
  the raw coordinates and accuracy. One anchor tag versus a ~150 KB tile
  library, an external tile host on every page load, and a new offline story for
  a PWA that currently precaches 82 entries and works offline by design.
* If an in-app map is wanted later, Leaflet drops in behind the same stored
  columns with no schema change. Nothing here forecloses it.

**Three constraints that must be designed for, not discovered:**

1. **Secure context.** `geolocation` requires HTTPS or `localhost`. Production
   (`https://gi.giinventory.com`) and dev (`localhost:5173`) both qualify;
   a bare-IP LAN test build would **silently fail**.
2. **Permission is per-user and revocable.** GPS is *best-effort*: a scan with
   a denied prompt must still record the movement, with `lat/lng NULL` and a
   note. Location capture must never block the location update.
3. **Privacy.** These coordinates are *where an employee was standing*. They go
   in `asset_movements`, they are visible to the roles that can see the asset,
   and the Auditor's write guard keeps them read-only. Worth an explicit line in
   the manual — this is the first genuinely personal data the system stores.

### 3.6 The "update location fast" screen

New page `/assets` (nav group **Records**, `minLevel: 0` read / `writes: true`
for the update action):

* search or scan → unit;
* one **Update location** action: rack picker (§4) *or* "use my GPS" *or* free
  text; optimistic write; a toast naming the new location;
* the last 5 movements inline.

Two taps from scan to saved. Offline queueing reuses the existing
`__giOffline` path that `offline-queue.spec.ts` already covers.

---

## 4. Phase 3 — Warehouse rack locator

### 4.1 Schema

```sql
CREATE TABLE storage_locations (
  id           serial PRIMARY KEY,
  "Site_ID"    text NOT NULL,
  code         text NOT NULL,        -- QR payload, e.g. 'A-03-2'
  zone         text,                 -- 'A'
  rack_no      text,                 -- '03'
  row_no       text,                 -- '2'
  bin_no       text,
  description  text,
  status       text NOT NULL DEFAULT 'active',
  created_at   timestamp DEFAULT CURRENT_TIMESTAMP,
  UNIQUE ("Site_ID", code)
);

CREATE TABLE material_locations (
  id          serial PRIMARY KEY,
  "Site_ID"   text NOT NULL,
  "SAP_Code"  text NOT NULL,
  location_id integer NOT NULL,
  is_primary  boolean NOT NULL DEFAULT true,
  updated_at  timestamp DEFAULT CURRENT_TIMESTAMP,
  updated_by  text,
  UNIQUE ("Site_ID", "SAP_Code", location_id)
);
CREATE INDEX ix_material_locations_sap ON material_locations ("SAP_Code", "Site_ID");
```

A material may sit in more than one rack (`is_primary` marks the one to walk
to first). This is deliberately **not** a column on `inventory` — `inventory`
already carries a `UNIQUE` on `Material_Code` (handover caveat 2) and is the
wrong grain for a many-to-many.

### 4.2 On "blazingly fast" — a measurement, not an adjective

`inventory` is **452 rows** at this site; `storage_locations` will be a few
hundred. An indexed lookup on `(SAP_Code, Site_ID)` against a table that size is
**sub-millisecond** and fits in a single page of shared buffers. No search
engine, no cache layer, no denormalisation is warranted, and adding one would be
the same mistake rule 11 was written to prevent — the overnight index work
**rejected four candidate indexes on evidence** for exactly this reason.

What I *will* do is prove it rather than assert it: an `EXPLAIN ANALYZE` on the
lookup, recorded in the run log, the same way the 20×/92×/6× index figures were.

The perceived speed is a **frontend** problem, and it is already solved: the ⌘K
palette ([CommandPalette.tsx](frontend/src/components/CommandPalette.tsx))
already debounces, already searches live stock by SAP/material/description, and
already scopes by site **server-side**. The locator becomes one more section in
that palette plus a `Rack` column on the Stock table — a store keeper types a
material name and reads `A-03-2` without leaving the page.

### 4.3 Endpoints

```
GET  /locations                       list/search racks
POST /locations                       create            (writes)
GET  /locations/lookup?q=|sap=|scan=  material → rack(s)   ← the hot path
PUT  /locations/material              assign/move a material  (writes)
GET  /locations/{code}/contents       scan a RACK → what's in it
```

`GET /locations/{code}/contents` is the reverse direction and costs nothing
extra: scanning the shelf's own QR answers "what is supposed to be here", which
is what makes a stock count fast.

QR sticker generation reuses `documents.py` `_qr_png` and the existing material
sticker layout — same generator, different payload.

---

## 5. Phase 4 — SME portal UX and reporting

### 5.1 "Select All" on multi-selects

Seven `mode="multiple"` selects in `frontend/src/sme/`:
`TotalOverview.tsx` ×3 (217, 222, 227), `ExecutionPlan.tsx` ×3 (532, 535, 538),
`SmartCalculator.tsx` ×1 (211). Four more elsewhere in the app.

**One shared component**, `sme/MultiSelectAll.tsx`, wrapping antd `Select` with
a `dropdownRender` header carrying **Select all / Clear** and an "n of N"
count — not seven copies of the same `onChange`. Applied to the SME seven now;
the other four adopt it for free later.

### 5.2 Table header alignment and column sizing

**Diagnose before changing CSS.** The likely mechanism: antd splits the header
into its own `<table>` when `scroll.x`/`scroll.y` is set, so header and body
columns only line up if widths are pinned — and mixing fixed-width columns with
`ellipsis` and a wrapping `materialCols.tsx` renderer inside one grid is exactly
the condition that drifts.

Plan: enumerate the SME grids, record which set `scroll`, which pin widths and
which leave columns unsized; fix the *pattern* centrally in
[`smartTable.tsx`](frontend/src/lib/smartTable.tsx) — where all 99 tables in 45
files already funnel — rather than sprinkling per-page CSS.

Constraint from rule 5: `materialCols.tsx` names **must keep wrapping**.
Truncation there ate the single character distinguishing
`CUMICRETE PU MF 300 (1MM) C` from its three siblings. "Auto-fit" must not
become "ellipsis".

Verification is visual, so this one gets Playwright screenshots at desktop and
tablet widths, plus a check that header and first body cell share an x-offset.

### 5.3 Hide the 1-SQM recipe from reports

Two places show it:

* [`SmartCalculator.tsx:184`](frontend/src/sme/SmartCalculator.tsx:184) — the
  **`Per SQM`** column (`dataIndex: 'for_1_sqm'`);
* the server-built `explanation` string
  ([sme.py:1407](backend/api/sme.py:1407)), which literally reads
  `0.35 KG/SQM × 1000 SQM = 350 KG` — the formula spelled out.

**Change:** drop the `Per SQM` column and the *rate* clause of `explanation`,
keeping the outcome (`350 KG → 7 × 50 KG pack(s) · in stock: 400 ✓`), and drop
the field from the calculator's xlsx/pdf export columns.

> ⚠️ **One honest limitation.** `For_1_SQM` **must remain** in the
> `/sme/snapshot` payload ([sme.py:182](backend/api/sme.py:182)): the TypeScript
> engine computes demand **in the browser**, so the rate has to cross the wire.
> "Backend-only" is achievable for the **reports and the UI** — which is what
> the brief is about — but not for the network payload, unless we move the whole
> cascade server-side (a far larger change that would end the TS↔PY parity gate).
> I am reading the requirement as "stop showing it"; say so if you meant "stop
> sending it".

### 5.4 Ordered Quantity back in the Session Report download

`Total_Procured_Qty` **already exists** on the model line
([engine.ts:150](frontend/src/sme/engine.ts:150)) — the subset-rule work
published it as its own named field precisely so consumers need not re-derive it.
`SessionReport.tsx` already exports `Pending Delivery` (tier 2) at lines
267/273/290.

**Change:** add `Total Procured` beside `Available` and `Pending Delivery` in
the three export blocks. Under the subset rule that is `max(available, ordered)`
— the **ceiling**, not a third bucket.

**The three rules this must not break, and how each is held:**

| Rule | Guard |
|---|---|
| **1c subset** | `Total_Procured_Qty` is taken from the engine, never computed as `available + pending` in the export layer. |
| **1b segregation** | It sits in the *quantity* block. It does **not** enter any readiness KPI and **nothing colours it green**. |
| **2 conservation** | `Demand = Allocated + Shortfall` is unchanged — this adds a column, not a term. |

A `test:ui-math` case asserts `Total_Procured == max(available, ordered)` on the
export rows, so an additive regression fails the gate rather than shipping.

---

## 6. Phase 5 — System polish

### 6.1 Auditor read-only visibility to HOD / SME — **frontend only**

Measured: `/hod/*` ([hod.py:31](backend/api/hod.py:31)) and `/sme/*`
([sme.py:46](backend/api/sme.py:46)) are `Depends(require_level(2))`;
`auditor` is **level 3** ([auth.py:95](backend/api/auth.py:95)). The API already
lets an auditor read them. Only `access: { anyRole: ['hod'] }` in
[`nav.tsx`](frontend/src/config/nav.tsx) hides the pages.

**Change:** add `'auditor'` to the `anyRole` list on the two group headers and
on the **read** children only — Executive Summary, Burn Rate, Lining Coverage,
Document Library, Low Stock, Purchase Requests, Cross-Site Requests, Estimator.

**Deliberately not granted:** anything marked `w()` (Approvals, Bulk Import),
because a `writes: true` page is unreachable for a view-only role by design; and
`/sme/master` + `/mh`, which are `require_roles("hod")` — a genuine backend
exact-lock, and both are CRUD surfaces.

`readonly.py` is untouched. Suite **BD**'s 36 checks still enumerate every
mutating route, so the write guard is unchanged and re-proven.

### 6.2 Login throttle beyond one process

Today `assert_login_allowed()` ([ratelimit.py](backend/api/ratelimit.py)) keeps
the 8-per-15-min budget in **process memory**, so N uvicorn workers means N × 8.

**Recommendation: a Postgres-backed budget, not Redis.** A tiny
`login_attempts (username_lc, window_start, failures)` table with an atomic
`INSERT … ON CONFLICT DO UPDATE … RETURNING` gives one shared counter across
workers using infrastructure that is **already deployed, already backed up, and
already in the runbook**. Redis would be a new service, a new failure mode and a
new thing to secure, for a counter that ticks a few times a minute.

Non-negotiable, from rule 10: it **throttles, never locks**. Budgets expire on
their own and no admin action is ever required to clear one — a per-account lock
is a DoS vector.

### 6.3 Automated Executive Summary email

**This is a channel addition, not a new cron.** `weekly_report.py` already runs
`weekly_report_loop()` every **Friday 17:00 Asia/Riyadh**, renders the Executive
Summary PDF via `exec_pdf.py`, stores it in `generated_reports` with a **72 h
expiring, sha256-hashed download token**, and dispatches to every active admin
and HOD (all-sites for admin, site-scoped per HOD) through
`services.notifications.dispatch()` — in-app bell + WhatsApp.

Missing: **email**. `email_outbox` and `services/emailer.py` (Phase 7b) already
exist.

**Change:** add email to that dispatch — the PDF as a real attachment (SMTP has
no 24-hour-window problem, which is why the WhatsApp path uses a link) plus the
same secure link as a fallback, honouring each user's delivery preference.
Schedule stays configurable through the existing `report_schedules` machinery
rather than a second timer.

### 6.4 Dashboard widgets — Top 5 Expiring, Highest Value

`dashboard.py` `/metrics` already returns KPIs plus valuation / stock-vs-min /
burn / top-consumed series. Two additions to the same endpoint (one round trip,
not two new ones):

* **Top 5 Expiring** — from `lots.expiry_date` (FEFO data already exists),
  ordered ascending, quantity remaining > 0. Consistent with the standing rule:
  **allow-and-log, never hard-block** — this is a *warning* widget.
* **Highest Value** — `Current_Stock × inventory.Unit_Cost`, top 5.
  ⚠️ `Unit_Cost` defaults to `0`, so the widget must state coverage
  ("*N of M items have a unit cost*") rather than presenting a confidently wrong
  total. Same principle as the "no GRAND TOTAL on the generic xlsx path" ruling.

Both respect site scoping server-side, like every other `/metrics` series.

---

## 7. Migrations — consolidated

One migration per phase, chained off the current head **`e7c3b95a41d2`**, each
mirrored in `backend/models.py` (rule 11), each with a working `downgrade()`.

| # | Revision | Contents |
|---|---|---|
| 1 | `…_consumption_type_and_tank_alias` | `consumption."Item_Type"`; `sme_tank_alias` |
| 2 | `…_asset_units_and_movements` | `asset_units`; `asset_movements`; 3 indexes |
| 3 | `…_storage_locations` | `storage_locations`; `material_locations`; 1 index |
| 4 | `…_login_attempts` | `login_attempts` (shared throttle) |

**Index discipline (rule 11):** the four above are on empty or near-empty
tables where the index *is* the access path (unique keys and the locator hot
path), so they are justified structurally. Any index proposed on an existing
large table gets benchmarked on an inflated clone first and is **rejected if
the planner does not use it** — as four candidates were in `e7c3b95a41d2`.

Ledger tables gain **no** unique constraint (rule 3).

---

## 8. Rule-preservation matrix

| Locked rule | Where this work could break it | The guard |
|---|---|---|
| **1c Subset** | New `Total Procured` export column | Read from the engine, never `available + pending`; new `test:ui-math` case asserts `== max(available, ordered)` |
| **1b Tier segregation** | Same column | Quantity block only; enters no readiness KPI; nothing colours it green |
| **1a SME⇄ERP decoupling** | Surface-Shield routing | Writes only `sme_consumption_log`; `sme_inventory_seed` untouched; suite BA grep extended; new BA byte-identical check |
| **1 Component identity** | Asset + locator keys | `asset_units` keyed `(Site_ID, SAP_Code, serial_no)`; locator keyed `(Site_ID, SAP_Code)`; nothing pools by `Material_Code` |
| **7 RBAC Auditor** | New POST/PUT routes (assets, locations) | `readonly.py` **untouched** — method-keyed middleware closes them by default. Suite BD proves it. **Nothing is added to the allowlist.** |
| **3 Excel sync** | New columns/sheets | Headers listed exactly; no Pandas; planners stay in `bulk_import.py`; one transaction; a missing quantity column is left alone, never zeroed |
| **4 Cutover blank-SAP rows** | `--sme-reseed` vs manual SQM edits | §2.5 — reseed preserves and reports operator edits (pending your decision) |
| **5 smartTable** | Header-alignment fix | Fixed centrally in `smartTable.tsx`; `materialCols` names keep **wrapping**, never ellipsis |
| **6 Both engines together** | — | **No engine change is proposed in any phase.** `engine.ts` / `sme_engine.py` and the golden are untouched. |
| **10 Throttle** | Shared budget | Still throttles, never locks; no admin clear |
| **11 Benchmarked indexes** | 4 new indexes | On new/empty tables where the index is the access path; `EXPLAIN ANALYZE` recorded |
| **`gi_database.db` untouchable** | — | No new-stack tooling writes it; sha256 re-verified each phase |

---

## 9. Gates — the contract for every phase

No phase merges below these. Current, verified 2026-08-04:

| Gate | Baseline | Expected movement |
|---|---|---|
| Backend service tests | **1094 / 0** | **rises** — new suites BF (sync/routing/alias), BG (assets/GPS), BH (locator) |
| Playwright E2E | **57 / 57** | **rises** — asset scan→update, rack lookup, auditor visibility, SME select-all |
| SME UI math | **27 / 0** | **rises** — `Total_Procured == max(available, ordered)` |
| SME TS↔PY parity | **1,313** | **unchanged** — no engine change |
| Legacy regression | **599 / 0** | **unchanged** — nothing in `legacy/` is touched |
| Frontend | `tsc -b` + build + `oxlint` clean | unchanged |
| Alembic | single head | **new head**, single, `downgrade()` tested |
| `gi_database.db` | sha `00652932…ba038` | **unchanged — re-verified each phase** |

Each phase = its own branch, its own PR, its own run log in `docs/`, in the
established house style (the ruling, the measurement, the revert-verification).

---

## 10. Suggested order, and why

1. **Phase 1 — sync + Surface-Shield routing + tank aliases.** Everything else
   is easier once the data path is right, and §2.3 needs operator input, so it
   should start earliest.
2. **Phase 5.1 Auditor + 5.4 Ordered Qty.** Hours, not days; both are
   high-confidence and unblock user-visible value immediately.
3. **Phase 3 — rack locator.** Self-contained; the store keeper's daily win.
4. **Phase 2 — assets + GPS.** The largest piece; depends on `storage_locations`
   from Phase 3.
5. **Phase 4 — SME UX polish.** Best done once the new columns and tabs exist,
   so alignment is fixed against the final grids rather than twice.
6. **Phase 5.2/5.3/5.4 — throttle, email, widgets.** Independent; good filler.

---

## 11. Decisions I need from you before Phase 1

1. **`TNK-091` → TRAIN J or TRAIN K?** 39 consumption rows, and it is genuinely
   ambiguous (§2.3). The alias table makes this answerable later rather than
   guessed now, but the first resolution is yours.
2. **Does SME consumption reduce SME availability?** My plan says **no** (rule
   1a). Confirm, or rule the overturn explicitly (§2.4).
3. **`--sme-reseed` vs manual SQM edits** — preserve-and-report (my
   recommendation) or treat manual edits as provisional? (§2.5)
4. **"Hide the recipe"** — hide from UI/reports (my reading), or also strip
   `For_1_SQM` from the snapshot payload? The latter means moving the cascade
   server-side and retiring the TS↔PY parity gate. (§5.3)
5. **Maps** — confirm native geolocation + a maps *link* (my recommendation), or
   do you want an embedded Leaflet map? (§3.5)

---

**The plan is ready for your review. No application code has been written; the
working tree still contains only the two files from the previous commit.**
