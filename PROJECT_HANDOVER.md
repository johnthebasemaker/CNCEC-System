# PROJECT HANDOVER — read this first

> **Updated 2026-08-05.** This is the fresh-session entry point: what is locked,
> where we are, and what happens next. Read this file, then
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (the system brain), then
> [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) (full state + gotchas) and
> [`REPO_MAP.md`](REPO_MAP.md) (the segregation contract).
>
> **Next phase: Feature Fine-Tuning and UI Polish.**
> **Hetzner production deployment is PAUSED by decision, not by blocker.**

---

## PAST — critical architecture rules, LOCKED

These are decisions that were each made *because the alternative was tried and
broke something*. Do not revisit them without an explicit instruction.

### 1. SME allocation is keyed on `(Material_Code, SAP_Code)` — never pool by code alone

*Locked 2026-07-30. Overturns the 2026-07-18 Material_Code pooling rule.*

A multi-part chemical system lists **one `Material_Code` as several distinct
physical drums**, separated only by the variant SAP — `GI-8005765` is Comp-A/B/C/D
at SAPs `1041` / `1041-1` / `1041-2` / `1041-3`. Each is a different container on a
different shelf.

* The engine key is `mat_key(Material_Code, SAP_Code)` → `"CODE|SAP"`, exposed on
  every allocation line as **`Material_Key`**.
* Each component gets its **own** available pool, on-order pool, shortfall and
  report row.
* SAP codes are **whitespace-normalized on both sides** of every join — the ERP
  writes `"1043 - 2"` for the recipe's `"1043-2"` (`sap_norm()` / `sapNorm()`).
* `sme_inventory_seed`'s primary key is **`(Material_Code, SAP_Code)`**
  (alembic `a4e9b1c73f28`).
* **No exceptions remain.** The Smart Calculator was the last holdout — it kept
  the 2026-07-18 `Material_Code` pooling until **2026-08-04**, when the operator
  authorized overturning it there too. `_CALC_POOL_SQL` now groups and joins on
  `(Material_Code, SAP_Code)`, and the UI displays the variant SAP. Live proof:
  on `GI-8005766` at 1,000 m², the catalyst `1042-3` holds 50.5 against a demand
  of 67.34 — pooled it saw 3,080.5 and the whole system reported **0 shortfall
  lines**.

**Why it is not negotiable:** pooling summed four unlike drums into one bucket, so
earlier recipe lines drained stock belonging to later ones. Measured on real data,
it reported components A and B as **fully covered while both were 10 short**, and D
as **10 short while it held three times what it needed**. The shortfall was not
imprecise — it was *inverted*, and SQM-achievable collapsed to 0 against a true 20.
Four `az-revert` checks in suite AZ pin each of those failures.

**Also locked:** `Material_Name + UOM` is **not** a valid discriminator. All four PU
rows in `For_1_SQM.xlsx` carry the same name *and* the same UOM; the names disagree
between the two workbooks; the UOM disagrees on 25 of 32 pairs. `SAP_Code` is the
only real discriminator.

### 1a. The SME estimator is STRICTLY DECOUPLED from the ERP ledger

*Locked 2026-08-02. Supersedes the 2026-07-28 effective-ordered netting (ruling Q2a).*

The estimator and the warehouse are **two separate pools of data, calculated
completely separately**. An ERP receipt, issue or return is a warehouse event and
must not move a single SME number.

* Every SME quantity comes from **`sme_inventory_seed`** — the data ingested from
  `Materials_DetailsAvailable_Qty.xlsx` — and from nowhere else.
* `available_qty` **is** `Initial_Available_Qty`; `ordered_qty` **is**
  `Initial_Ordered_Qty`. No derivation, no join.
* `receipts` / `consumption` / `returns` / `inventory` are **never named** by
  `SQL_SME_MATERIALS` or by `_CALC_POOL_SQL` (Smart Calculator). Suite BA greps for
  them, and — more to the point — posts real movement against an SME material's own
  SAP and requires **every SME read to come back byte-identical**.
* The snapshot the browser engine consumes carries **no `received_qty` /
  `consumed_qty`** field at all.

**What this overturns.** The two places the ledger used to leak in were
`SQL_SME_MATERIALS` (`available = seed + Σreceipts − Σconsumption`, joined through
the ERP `inventory` master) and the Smart Calculator, whose entire stock pool was
`Σreceipts − Σconsumption − Σreturns`.

**And why the netting had to go with it.** `effective_ordered =
max(Initial_Ordered_Qty − Σreceipts, 0)` existed *only* because arriving goods
inflated `available_qty` through the ledger, so counting the order as well
double-counted them. With nothing inflating availability, subtracting receipts
would strip units from the order that were never added — `GI-8005762` would lose
the 10,920 it had received against an order of 95,200. Both engines now take
`ordered_qty` at face value, clamped at zero. The parity fixture deliberately still
carries `received_qty` values (M1 = 40, M2 = 15, M6 = 25) as **poison pills**: the
golden proves both engines *ignore* the field rather than merely proving it is
absent from the payload.

Decoupled is not frozen — editing `sme_inventory_seed` (what the Excel sync writes)
still moves every SME number at once. Suite BA pins that too.

### 1c. THE SUBSET RULE — Available is part OF Ordered, not extra to it

*Locked 2026-08-05 (operator correction on the shape of the source data).*

In `Materials_DetailsAvailable_Qty.xlsx`, **`Ordered_Qty` is the TOTAL quantity
PROCURED for the project** and **`Available_Qty` is the portion of it that has
physically ARRIVED**. Available is a SUBSET of Ordered, never a second bucket
beside it.

```
tier 1  = available_qty
tier 2  = max(ordered_qty − available_qty, 0)     the PENDING DELIVERY
ceiling = tier1 + tier2 = max(available, ordered) = Allocated_Qty
to buy  = max(demand − max(available, ordered), 0) = Shortfall_Qty
```

Everything but tier 2 falls out of the existing cascade arithmetic; only
`build_model` changed in each engine.

**Why it is not negotiable:** the engine added the two buckets, double-counting
every unit already on the shelf. Verified on all 32 workbook rows — `available ≤
ordered` everywhere, **14 of 32 fully delivered**. Reconciled against
`dashboard_material_balance.xlsx`: **22 of 30 report rows had an understated buy
list, 22,951 units in total**. `GI-8005763` (143,000 arrived of 143,000 ordered)
read **286,000**, reported **nothing to buy** against a demand of 152,685, and
hid a **9,685-unit shortage** entirely. Suite **BC** (16 checks, two of which
read the live workbook) and `test:ui-math` §E pin it.

**Naming:** the tier-2 quantity fields are `Alloc_Pending` /
`pool_pending_init` / `Pending_Delivery_Qty`, and every report states
`Total_Procured_Qty` beside them. The `*_With_Ordered_*` coverage fields keep
their names — they measure against the *total procured*, which is what they
always meant — and the UI labels them **"When delivered"**. UI wording: second-
tier quantities read **"Pending Delivery"**, second-tier coverage reads **"When
delivered"**.
Full account: [`docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md`](docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md).

### 1b. STRICT TIER SEGREGATION — a purchase order is never readiness

*Locked 2026-08-03.*

**TIER 1** (`Alloc_Available` / `pool_init`) is the ONLY input to readiness:
`Status`, `Completion_Pct`, `SQM_Achievable_Now`, `Coverage_Now_Pct`,
`Fulfillment_Pct`, `SQM_Deficit`, and every "can we build it today" answer.

**TIER 2** (`Alloc_Pending` / `pool_pending_init`) feeds ONLY the forward-looking
twins — `Completion_With_Ordered_Pct`, `SQM_Achievable_With_Ordered`,
`Coverage_With_Ordered_Pct`, `Fulfillment_With_Ordered_Pct` — and the NET buy
list (`Shortfall_Qty`), so stock already ordered is not ordered twice.

**`Allocated_Qty` (= tier 1 + tier 2) is a CONSERVATION field.** It exists so
`Demand = Allocated + Shortfall_Qty` holds. It is NOT a readiness quantity, and
`Allocated_Qty / Demand_Qty` is NOT a coverage percentage anything may colour
green. Every tier-2 number a consumer could want is published as its own named
field precisely so nobody re-derives one from it.

**Why it is not negotiable:** the engine always had this right; six presentation
layers above it did not. `PHENACIN ACP POWDER` (0 available, 56,350 on order)
made **18 of 85** (tag, code) units render a green "100% Fully Ready" pill they
had not earned, listed them under the *Fully Ready* filter, and overstated
buildable area by **9,118 m² — 21.5 % of the remaining programme**. Suite BB
(18 checks) and `tests/e2e/specs/sme-tiers.spec.ts` (4 tests) pin it end to end.
Full account: [`docs/SME_TIER_SEGREGATION_RUNLOG.md`](docs/SME_TIER_SEGREGATION_RUNLOG.md).

Every SME tab shows the two tiers in separate columns, under a shared `TierNote`
legend: **green = available now · amber = on order · red = still to buy**.

### 2. Two-tier allocation field contract

*Locked 2026-07-28 (rulings Q1/Q6). The Q2a netting half is superseded — see 1a;
which figure each tier may drive is 1b.*

Companion field contract, conserved on every line:

```
Demand_Qty      = Allocated_Qty + Shortfall_Qty
Allocated_Qty   = Alloc_Available + Alloc_Pending   (= the total procured)
Shortfall_Available_Qty = the PHYSICAL gap  → drives feasibility / "Ready to Build"
Shortfall_Qty           = the NET gap       → drives the buy list
```

Feasibility judges **tier 1 only**: stock on a truck cannot be applied to a tank
today.

### 3. `tools/pg_excel_sync.py` — native upserts, no Pandas

* **SME by default; the ERP half is opt-in** *(2026-08-02, with rule 1a)*. The
  everyday command syncs `sme_recipe` / `sme_equipment` / `sme_inventory_seed` only:

  ```bash
  DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
  .venv/bin/python tools/pg_excel_sync.py --site CNCEC [--commit]
  ```

  It prints `scope : SME tables only — the ERP warehouse is untouched`, and never
  opens `CNCEC_Inventory.xlsx`. Writing the live warehouse master or appending to
  its ledger requires `--erp` (or naming `inventory` / `ledger` in `--kinds`),
  which prints `⚠️ ERP + SME — this run WRITES the live warehouse`.
* **Header names must be listed exactly.** `bulk_import._col` matches
  case-insensitively but does **not** normalise spaces vs underscores. The live
  `Materials_DetailsAvailable_Qty.xlsx` ships **`Available Qty`** (space) beside
  `Ordered_Qty` (underscore); with only the underscored alias the column resolved
  to `None` and all 30 materials re-baselined to the `0.0` that summing no cells
  produces. A quantity column the workbook omits is now **left alone** (the field
  is dropped from the plan, so `COALESCE` keeps the stored value) and reported as
  a warning, instead of silently zeroing.
* Every master-data write is `INSERT … ON CONFLICT (<natural key>) DO UPDATE` with
  `COALESCE(excluded.col, table.col)`, so a blank workbook cell never erases data.
* **Pandas is deliberately not used.** It is not in `backend/requirements.txt` (it
  arrives only transitively via streamlit for the legacy app), so a production-path
  tool must not import it. The reader is openpyxl via `bulk_import.py`.
* **Column-mapping logic is never duplicated** — the planners live in
  `backend/api/bulk_import.py` and this tool replaces only the *write* path.
* The whole sync is **one transaction** across all five kinds; a failure anywhere
  rolls everything back.
* It refuses to run against anything that is not a Postgres URL, and refuses
  outright if the URL mentions `gi_database`.
* Ledger tables (`receipts`/`consumption`/`returns`) have **no** unique constraint
  and must never get one — the same (date, SAP, qty) line can legitimately repeat.
  Idempotency there comes from `plan_ledger`'s three-tier reconcile.

### 4. The cutover protects the 86 blank-SAP legacy recipe rows

The frozen legacy SQLite `sme_recipe` and `sme_inventory_seed` **have no `SAP_Code`
column at all**. A cutover therefore lands 86 recipe rows with no SAP and every
material as one blank-SAP seed row.

* Those 86 rows are **real recipe data the workbook does not cover** — measured:
  they are *disjoint* from the 30 workbook-coded `(code, material)` pairs, zero
  overlap. **Nothing deletes them.**
* Blank-SAP *seed* placeholders are retired only when **both** hold: (a) the
  workbook supplied a real SAP for that `Material_Code`, **and** (b) no blank-SAP
  *recipe* line still references it. Guard (b) was learned the hard way — without
  it, coverage collapsed to **0.0% across all 29 equipment**, because those recipe
  lines can only draw on a blank-SAP seed row.
* The documented remedy for a mixed state is the **SME reseed**
  (`pg_excel_sync --sme-reseed`), which replaces both sides from the workbook and
  converges to `sme_recipe` 41 rows / `sme_inventory_seed` 32 rows, zero blanks.

### 5. Global tables render through `smartTable.tsx`

`frontend/src/lib/smartTable.tsx` is a drop-in replacement for antd's `Table`.
All 99 `<Table>` instances across 45 files import `Table` from there instead of
from `'antd'`. It derives sorters and filters from the column definitions with
**no change at the call site and no added chrome**.

Four rules it encodes:

| Rule | Why |
|---|---|
| No `dataIndex` → no sorter | An "Actions" column of buttons has nothing to sort by |
| Numeric → sorter, no filter | A quantity is a measurement, not a category |
| Boolean → sorter, no filter | A "true/false" dropdown rarely matches what the cell renders |
| **Server-paginated → untouched** | Sorting one page of 20 out of 5,000 *looks* like it works and silently lies |

Server-side paging is **auto-detected** from controlled pagination (`total` **and**
`current` both set); four grids match and all four opt out. Filters cap at 30
distinct values and grow a search box above 8. An explicit `sorter`/`filters` on a
column always wins; `smart={true|false}` overrides the sniff.

**Companion:** `frontend/src/sme/materialCols.tsx` renders material components —
the variant SAP under the code, and names that **wrap rather than ellipse**
(truncation was eating the single character that distinguishes
`CUMICRETE PU MF 300 (1MM) C` from its three siblings).

### 7. Exports render through ONE engine each; Auditor is view-only

*Added 2026-08-03 (`feat/exports-roles-and-sysadmin`).*

* **Every tabular PDF goes through `backend/api/pdf_tables.py`.** Columns are
  measured and allocated MAX-MIN FAIR (water-filling), cells WRAP, and nothing
  is truncated. The old `reports.to_pdf` split the page into equal widths and
  cut at `[:18]`/`[:24]` — fpdf's `cell()` does not clip, so a long description
  was drawn **4.1 mm on top of its neighbour** while `Date` wasted 20 mm, and
  28 characters were destroyed outright. `exec_pdf` delegates its tables here.
  ⚠️ `multi_cell` reserves `pdf.c_margin` on each side that `get_string_width`
  does not know about; the wrap width must add it back.
* **Every xlsx goes through `backend/api/xlsx_style.py`** (openpyxl) or
  `sme_export_layouts.py` (xlsxwriter) — same geometry, same palette, both
  branded: rows 1-4 logo + meta, row 5 title bar, row 6 header, row 7+ data.
  **Header is row 6, data row 7** — tests that read row 1 will fail.
  No GRAND TOTAL on the generic path: summing `Days_Overdue` is a wrong number
  stated confidently.
* **`auditor` (level 3) reads what its level reaches and writes NOTHING.**
  Enforced once, in `backend/api/readonly.py`, as method-keyed ASGI middleware —
  **never per-endpoint**, because a forgotten annotation fails OPEN. 126 of 143
  mutating routes blocked; the 17 exceptions are session/self-service/compute
  and are listed in that module. Level 3 is required, not generous: an unscoped
  account carries `site_id = ''`, so a level-2 auditor would fail closed and see
  nothing. In the UI, `writes: true` on a nav rule makes a page unreachable for
  the role; `useReadOnly()` disables the rest. Suite **BD** (36 checks)
  enumerates every mutating route, so a new `@router.post` fails it by default.
* **`legacy/config.py` stays frozen at six roles.** The auditor is new-stack
  only.

### 8. Local operations: `bin/power.sh` and `bin/backup_db.sh`

* `./bin/power.sh sleep|wake` stops/starts Postgres + the root cloudflared
  daemon for battery. Measured: those two cost **0.0 %** and **0.1 %** CPU idle.
  The real drain is `com.gi.whatsapp-worker` and two sibling LaunchAgents from
  the pre-cutover stack — their programs were deleted on 2026-07-13 but
  `KeepAlive{Crashed:true}` keeps respawning them: **~2,880 failed Python
  launches a day**. `./bin/power.sh reap` unloads them (`restore` undoes it).
* `./bin/backup_db.sh` snapshots `gihub` (plain-SQL pg_dump, gzipped) plus a
  read-only copy of `gi_database.db`, into gitignored `.backups/`, keeping 14 of
  each. Verified by restoring into a throwaway DB: 74 tables and every row count
  exact. `--install` runs it daily at 02:00 as `com.gi.hub-backup`, **replacing
  `com.gi.backup`, which had been failing silently for 25 nights** — there were
  no local backups at all.

### 9. Manual parsing is FENCE-AWARE; the assistant RETRIEVES

*Added 2026-08-04 (`feat/overnight-autonomous-polish`).*

* **Never parse `USER_MANUAL.md` chapters with a bare `^# \\d+\\.` match.** §17
  documents shell scripts whose comments read `# 1. Pull the new code`, and a
  line-by-line matcher read those as chapters 1-4 — overwriting Introduction,
  Roles & Permissions, Login and the Store Keeper Manual for EVERY role, and
  putting `launchctl` and the developer's iCloud paths into a Store Keeper's
  assistant context. Use `ai/manual_index.iter_chapters()`, which tracks fences
  and keeps the FIRST occurrence of a number. `build_manual_pdf` has its own
  copy of the fence logic (it must stay importable without the backend package);
  suite BE pins both.
* **The Hub Assistant retrieves, it does not stuff.** BM25 over ~390 chunks,
  no vector store and no new dependency. Admin prompts went 178 KB → 4 KB
  (97.7%). The role filter runs BEFORE scoring — that is the security boundary,
  and it must stay that way.
* **Every role in ROLE_META needs an entry in `_ROLE_ALLOWED`.** `auditor` was
  missing and silently inherited the Store Keeper's chapters. Unknown roles fall
  back to the LOWEST allowlist, never the highest. Suite BE asserts both, and
  asserts that every chapter any allowlist references actually exists.
* Manual chapters are now **21**; §20 is the Auditor manual and §21 the 2026-08
  feature update. Regenerate PDFs with `.venv/bin/python build_manual_pdf.py --role all`.

### 10. Auth: idle sign-out + a PER-ACCOUNT login throttle

* 30 minutes idle signs the user out; the client timer is the trigger but the
  REVOCATION is what matters — it calls the ordinary logout, which kills the
  refresh family server-side. Cross-tab via a localStorage timestamp.
* `rate_limit(10, 60)` on `/auth/login` is keyed by **IP** and is therefore
  blind to credential stuffing spread across hosts.
  `ratelimit.assert_login_allowed()` adds a per-USERNAME failure budget (8 per
  15 min), checked before the bcrypt verify, cleared by a correct password, and
  applied to a wrong TOTP too. It THROTTLES rather than LOCKS on purpose — a
  per-account limit is a DoS vector, so it must never need an admin to clear.
  Relaxed under `GI_DOTENV=0` like the other strict limits.

### 11. Indexes are benchmarked before they are added

Alembic `e7c3b95a41d2` added 7 hot-path indexes (ledger `(SAP_Code, Site_ID)`
and `Date`, audit `action_type`) after measuring on a clone inflated to 260k/
240k/429k rows: 20x, 92x, 6x. **Four candidates were rejected on evidence** —
`system_audit_log (id DESC)` and `(username, id DESC)` cost 9.5 MB each for ZERO
planner uses (the pkey already scans backwards), the ledger `TRIM()` expression
indexes lose to `(SAP_Code, Site_ID)`, and `inventory` is 442 rows. Mirror any
new index in BOTH `models.py` and a migration. Ledger indexes are never UNIQUE.

### 6. Standing rules that predate this session (still binding)

* **Both SME engines change together.** `backend/api/sme_engine.py` and
  `frontend/src/sme/engine.ts` are line-for-line mirrors proven equal against
  `sme_parity_fixture.json` → `sme_parity_golden.json`. Any numeric change =
  change BOTH + regenerate the golden in ONE commit.
* **Half-up rounding** `floor(x·10ⁿ + 0.5)` is shared verbatim across both
  languages. Never "fix" it to half-even.
* **STRICT BOTTLENECK** (2026-07-07): a unit's coverage is its least-available
  material's rate, never the Σalloc/Σdemand average. **Extended 2026-08-04 to
  every SCOPE-wide KPI**: a scope's coverage is `Σ buildable m² ÷ Σ remaining m²`
  (`session.ts scopeBottleneckCoverage`), never a quantity average across unlike
  materials. The Session and Location reports used the quantity average and read
  **57.7 %** on the live model where the true figure is **7.8 %** — TRAIN K read
  54.6 % against a real 0.4 %. Both now call one shared helper. Gated by
  `npm run test:ui-math`.
* **Unmodelled is never 100%** (ruling Q5): a system code with no recipe rows
  scores 0 SQM achievable, never a silent full-ready.
* **FEFO + over-issue stay allow-and-log** — never add a hard block.
* **Never delete `system_audit_log` rows** — audit assertions are delta-counted.
* **`gi_database.db` is untouchable** — never staged, never written by new-stack
  tooling. Verify its sha256 is unchanged after any data work.
* `sme_inventory_seed` never mingles with the ERP `inventory` table (SME Canon
  Rule 2) — since 2026-08-02 this is the *narrow* case of rule 1a.

---

## Running it locally — one command

`bin/dev.sh` raises and levels the whole stack (Postgres + FastAPI + Vite +,
where it applies, the Cloudflare connector). Three environments, one at a time —
they all want `:5173`:

| Command | Serves | Connector |
|---|---|---|
| `./bin/dev.sh localhost` | `http://localhost:5173` (HMR live) | none |
| `./bin/dev.sh tunnel` | `https://local.giinventory.com` | starts `gi-hub` from `deploy/cloudflared/config.yml` |
| `./bin/dev.sh gi` | `https://gi.giinventory.com` (legacy mirror) | none — the **root LaunchDaemon** already serves it |

```bash
./bin/dev.sh stop
```

`stop` kills the API, Vite and *our* connector — process-group signals plus a
repo-scoped sweep, so uvicorn's reloader child and Vite's node child go too —
and then reports whether `:8000` and `:5173` are actually free. Also
`./bin/dev.sh status` and `./bin/dev.sh logs [api|web|tunnel]`.

Two things it will not do, on purpose: it leaves **Postgres** running (a shared
brew service the legacy app and every suite use — `stop --db` if you really mean
it), and every sweep is scoped to your uid so the **root cloudflared daemon**
that serves `gi.giinventory.com` can never be caught in it. Starting a second
connector is how Error 1033 came back before; `tunnel` mode refuses to start
when one of yours is already up. Details: [`deploy/cloudflared/README.md`](deploy/cloudflared/README.md).

---

## PRESENT — current state and baselines

All green locally at commit `fae0b3f` (main), verified 2026-07-30.

| Gate | Result | Command |
|---|---|---|
| Backend service tests | **1094 / 0** (suites A…BE) | `GI_DOTENV=0 .venv/bin/python -m backend.api.service_tests` |
| Playwright E2E | **57 / 57** (~24 s, own throwaway DB) | `cd tests/e2e && npm test` |
| SME TS↔PY parity | **1,313 comparisons** | `npm run parity:sme --prefix frontend` |
| **SME UI math** (session.ts + insights.ts) | **27 / 0** | `npm run test:ui-math --prefix frontend` |
| Legacy regression | **599 / 0** | `.venv/bin/python legacy/bug_check.py` |
| Derived-view parity | **5 / 5** ⚠️ fresh cutover only | `DATABASE_URL=… .venv/bin/python tools/parity_check.py` |
| Frontend | `tsc -b` + `npm run build` + `oxlint` ✅ | `npm run build --prefix frontend` |
| Alembic | single head **`e7c3b95a41d2`** (`hot_path_indexes`) | see ARCHITECTURE §8 |
| `gi_database.db` | sha256 `00652932…ba038` **unchanged** | `shasum -a 256 gi_database.db` |

**Local Cloudflare tunnelling is resolved and stable.** Verified 2026-07-30: a
single managed tunnel is running (the root LaunchDaemon,
`/Library/LaunchDaemons/com.cloudflare.cloudflared.plist`). The two rogue
user-level instances that caused the recurring **Error 1033** are gone, and the
dormant user LaunchAgent `com.gi.cloudflared` is unloaded. Diagnosis and the exact
recovery commands live in [`deploy/cloudflared/README.md`](deploy/cloudflared/README.md).

### What shipped most recently (this session, merged to main)

| PR | Commit | What |
|---|---|---|
| — | `fix/sme-ordered-subset-rule` | **The subset rule** (rule 1c) — `Ordered_Qty` is the TOTAL procured and `Available_Qty` a subset of it, so tier 2 is `max(ordered − available, 0)`; the additive reading understated the buy list by 22,951 units and hid a 9,685-unit shortage on GI-8005763. Suite **BC** (16) + `test:ui-math` §E (7) |
| — | `fix/sme-final-math-alignment` | **Final math alignment** — scope-wide coverage → area-weighted bottleneck (57.7% → 7.8% on live data); Smart Calculator → `(Material_Code, SAP_Code)`; new `npm run test:ui-math` gate (20 checks) for the previously untested presentation layer |
| — | `fix/sme-strict-tier-segregation` | **SME tier segregation** (rule 1b) — readiness is TIER 1 everywhere; `session.ts codeStats`, Total Overview, Location Report, Execution Plan, Dashboard, exec summary + PDF, Smart Calculator, location export all split; `insights.ts` Material_Key lookup bug fixed; suite **BB** (18) + 4 E2E |
| — | `feat/sme-strict-decoupling` | **SME ⇄ ERP strict decoupling** (rule 1a) — ledger stripped from `SQL_SME_MATERIALS` + Smart Calculator, effective-ordered netting removed from both engines, golden regenerated, suite **BA** (11 checks), `pg_excel_sync` defaults to SME-only, `Available Qty` header alias |
| #15 | `7868e8d` | **Project-wide table sorting + filtering** — `smartTable.tsx`, 99 tables / 45 files, +3 E2E specs |
| #16 | `c62c415` | **SME component pooling fix** — `(Material_Code, SAP_Code)` everywhere, suite AZ (20 checks), alembic `a4e9b1c73f28` |

Immediately before those, on the same programme:

* **Two-tier allocation + reverse SQM** (`docs/SME_SQM_BOTTLENECK_RUNLOG.md`) —
  Available vs Ordered split, SQM-achievable bottleneck maths, Material-Wise
  Segregated Report.
* **Session-report aggregation** (`docs/SESSION_REPORT_SUMMARY_RUNLOG.md`) —
  Total Material Demand sheet leads the workbook and the PDF.
* **`tools/pg_excel_sync.py`** (`docs/PG_EXCEL_SYNC_RUNLOG.md`) — the atomic,
  idempotent, Postgres-native Excel sync.

### Live CNCEC numbers — decoupled, after the 2026-08-02 workbook re-sync

Both workbooks were re-synced on 2026-08-02 (`Equipment.xlsx` 58 rows changed,
`Materials_DetailsAvailable_Qty.xlsx` 30 rows changed, 0 rejects, re-run is a
no-op). These figures are now driven **only** by `sme_inventory_seed`.

| | Value | Previously |
|---|---|---|
| Component stock pools | **32** | 32 |
| Allocation lines | 352 | 352 |
| Material rows in reports | **30** | 30 |
| Remaining SQM | **42,403** | 49,435 |
| SQM achievable **now** | **3,312** (7.8%) | 2,778 (5.6%) |
| SQM achievable **with on-order** | **12,430** (29.3%) | 24,743 (50.1%) |
| Seed totals | 358,684 available · 483,196 on order | — |
| Multi-component materials | 4 (`GI-8005764/65/66/67`) | 4 |

The "with on-order" figure fell because the workbook's own `Ordered_Qty` column is
now the whole story — the old number was inflated by ERP receipts flowing into
availability on top of a full order.

### Known caveats carried forward

1. **Stock UOM is display-only** (ruling Q2). Recipe rates are per-SQM in `KG`
   while stock is in `Can`/`BAG`/`DR`/`EA` for 25 of 32 pairs, and `PACKAGE SIZE`
   is blank for every PU component — there is no conversion factor in the data.
   Quantities are taken to be in the recipe's unit. A real pack-size table is
   outstanding.
2. **The ERP `inventory` table has a UNIQUE on `Material_Code`**, so the variant
   SAPs cannot all carry it there — the sync reports this per SAP. Pre-existing and
   untouched; it means the *ERP* side still cannot express component identity.
   Wants its own decision.
3. **`tools/parity_check.py` fails against the live mirror BY DESIGN** — Postgres
   is permanently ahead of the frozen SQLite since the Excel injection. It is
   meaningful only on CI or a freshly-reloaded/cutover mirror. Since rule 1a its
   `sme_materials` rollup no longer compares `received_qty` / `consumed_qty`, and
   its `available_qty` comparison additionally asserts that the frozen dataset
   carries no ERP movement against an SME material (true on a fresh cutover). The
   decoupling itself is pinned by **suite BA**, which does not depend on that.
4. **`postgres-dual-ci.yml` has never passed on the GitHub runner** (always at the
   `legacy/bug_check.py` step) while the same tree passes 599/0 locally under every
   simulated CI condition. Cause is Linux-runner-specific and unresolved; failures
   now surface as `::error::` annotations plus uploaded artifacts.
5. **The dev database has been migrated and re-synced.** `alembic upgrade head` +
   a `sme-materials` workbook sync were applied to `:5433/gihub` so the suites
   could run against the new primary key. Rebuildable; the migration has a working
   `downgrade()`.

---

## FUTURE — what happens next

### Immediate next phase: Feature Fine-Tuning and UI Polish

The next session's focus. No deployment work. Suggested starting points, none of
them committed to:

* The four multi-component materials now render as 4 rows each — worth an operator
  read-through of the SME Session Report, Execution Plan and Procurement views to
  confirm the density is right.
* `smartTable` filter labels come from the **raw** field value, so a column whose
  `render` maps codes to friendly labels lists the codes in its dropdown. Only
  `UsersPage` has been given an explicit `filters` override so far; other columns
  with label-mapping renders may want the same treatment.
* The stock-UOM display mismatch (caveat 1) is now visible in the UI.

### Phase 3 — Hetzner Ubuntu Docker deployment: **PAUSED**

Paused by decision on 2026-07-30, **not blocked**. Everything needed is built and
documented; it will be executed only after fine-tuning is complete.

When it resumes, the runbook is [`tools/migration/README.md`](tools/migration/README.md)
and the kit is [`docs/DEPLOY.md`](docs/DEPLOY.md) + [`deploy/`](deploy/). Open
operator items at the time of pausing:

* **Server side:** generate strong `JWT_SECRET` and `POSTGRES_PASSWORD` (both are
  `CHANGE_ME` in `deploy/.env`); set `GI_ENV=production` to arm the JWT boot guard.
* **Meta side:** approve the `gi_evening_summary` template (2 body vars, lang
  `en`); subscribe the webhook URL. The other four templates are LIVE.
* **Cloudflare (one-time, for the native apps):** a Zero Trust Access application
  for `gi.giinventory.com/api/*` with a **Bypass (Everyone)** policy — without it
  every native API call dies as a CORS-killed 302. Details: `docs/NATIVE_APPS.md` §6.
* ⚠️ **The tunnel token is passed as a command-line argument**, so it is visible in
  full to any local process via `ps aux`. Consider rotating it and moving it into
  the plist's `EnvironmentVariables` rather than `ProgramArguments`.

---

## Run logs — the detailed history

Each recent programme has its own run log with the rulings, the maths, the
revert-verification and the caveats:

| Log | Covers |
|---|---|
| [`OVERNIGHT_OPTIMIZATION_RUNLOG.md`](OVERNIGHT_OPTIMIZATION_RUNLOG.md) | Rules 9-11: the manual fence bug, BM25 retrieval, idle logout, per-account throttle, benchmarked indexes, ⌘K material search |
| [`docs/EXPORTS_ROLES_SYSADMIN_RUNLOG.md`](docs/EXPORTS_ROLES_SYSADMIN_RUNLOG.md) | Rules 7-8: the PDF overlap measurements, the global xlsx template, the Auditor role, power.sh + backup_db.sh |
| [`docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md`](docs/SME_ORDERED_SUBSET_RULE_RUNLOG.md) | Rule 1c: the double-count, the 22,951-unit buy-list correction, suite BC |
| [`docs/SME_FINAL_MATH_ALIGNMENT_RUNLOG.md`](docs/SME_FINAL_MATH_ALIGNMENT_RUNLOG.md) | The last two loopholes: scope-wide bottleneck + calculator component identity |
| [`docs/SME_TIER_SEGREGATION_RUNLOG.md`](docs/SME_TIER_SEGREGATION_RUNLOG.md) | Rule 1b: the six layers that merged the tiers, the 21.5% measurement, the per-tab audit |
| [`docs/SME_STRICT_DECOUPLING_RUNLOG.md`](docs/SME_STRICT_DECOUPLING_RUNLOG.md) | Rule 1a: the two ledger leaks, suite BA, the CLI + header fixes, the re-sync |
| [`docs/SME_COMPONENT_POOLING_RUNLOG.md`](docs/SME_COMPONENT_POOLING_RUNLOG.md) | The `(Material_Code, SAP_Code)` ruling, end to end |
| [`docs/TABLE_TOOLS_RUNLOG.md`](docs/TABLE_TOOLS_RUNLOG.md) | `smartTable.tsx` and the four rules |
| [`docs/SME_SQM_BOTTLENECK_RUNLOG.md`](docs/SME_SQM_BOTTLENECK_RUNLOG.md) | Available vs Ordered, reverse-SQM bottleneck |
| [`docs/SESSION_REPORT_SUMMARY_RUNLOG.md`](docs/SESSION_REPORT_SUMMARY_RUNLOG.md) | Total Material Demand aggregation |
| [`docs/PG_EXCEL_SYNC_RUNLOG.md`](docs/PG_EXCEL_SYNC_RUNLOG.md) | The atomic Excel → Postgres sync |
| [`docs/POSTGRES_MIGRATION.md`](docs/POSTGRES_MIGRATION.md) §8 | The full per-slice project history |
