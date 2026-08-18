# PROPOSED — PHASE 7

**Status: PLAN ONLY. No application code has been modified.**
Written 2026-08-18 against `main` @ `0cd0ac3`, Alembic head `c7a93e5d2b18`.

> **Read §9 before you read anything else if you are short of time.** Two of the
> findings there are not "risks" — they are *already true* of the files sitting
> in the repo root, and both fail **silently**. A Phase 7 that starts with UI
> work will lose master data before it writes a line of it.

---

## 0. How this was verified

Everything below is evidence, not inference. The four workbooks were parsed with
a throwaway script (`scratchpad/probe*.py`, not committed) and joined against
each other; the code claims were grepped and, where behavioural, executed.

| Claim | How it was checked |
|---|---|
| Workbook shapes, keys, joins, arithmetic | pandas parse + set algebra of all four files |
| Production lot/expiry emptiness | `zcat .backups/gihub_2026-08-17_020932.sql.gz`, `COPY` blocks counted |
| `dev.sh` behaviour | the real script run under bash 3.2 and 5.x with PATH stubs |
| Every `file:line` | grepped and read in place |

Postgres was **not** running during this session, so DB claims come from the
2026-08-17 dump rather than a live query. Nothing was started or written.

---

## 1. What the four workbooks actually contain

### 1.1 `Equipment.xlsx` — sheet `Data Input`, 292 rows × 22 cols

`Lining_System_Code` is now **`LSC1`…`LSC10`** (10 distinct). Also present:
`Lining_System_Short_Name`, `Lining_Type`, `Lining_System`, `Material Spec.`,
`Lining_Area/location`, `Surface_Area_SQM`, `Equipment Total SQM`.

| Observation | Count | Consequence |
|---|---|---|
| Rows with **no** `Equipment_Tag_No.` | **33** | SK "select Equipment No" has 33 unpickable areas (§9.6) |
| Distinct `Equipment_Tag_No.` | 26 | one tag carries many area rows |
| `(Tag, LSC)` pairs duplicated | **52 of 69** | by design — `Shell`, `Bottom`, `Baffle`, `Grade Slab Part-1/2` are separate rows aggregated by the importer |
| `WBS #` non-null | **0** | column is entirely empty |
| `Location` raw / normalised | 5 / **3** | `'TRAIN J '`, `'TRAIN K'`, `'TRAIN K '`, `'Brown Field '`, `'BROWN FIELD'` |

`_LOCATION_CANON` (`backend/api/bulk_import.py:107`) already folds those five to
three, and `bulk_import.py:1283-1284` already backfills a missing tag from
`Name`. Both behaviours are load-bearing for Phase 7 and must not be "cleaned up".

### 1.2 `For_1_SQM.xlsx` — sheet `LINING SYSTEM MATERIAL CONSM`, 46 rows × 17 cols

**New column: `Execution_Sub_Activity_Code`** — `ESC11`…`ESC111`, 18 distinct.
`Lining_System_Code` is `LSC1`…`LSC11` (11 — one more than Equipment: **`LSC11`
has a recipe but no equipment**).

The 18 `(LSC, ESC)` pairs map cleanly onto four `Subactivity` labels: Primer
Application, Lining Application, Screed Application, Seal Coat Application.

### 1.3 `Materials_DetailsAvailable_Qty.xlsx` — sheet `Materials`, 33 rows × 16 cols

Unchanged in shape. Confirms the rule-1 identity trap is still live — four
`Material_Code`s each carry multiple `SAP_Code`s:

```
GI-8005764 → 1040, 1040-1
GI-8005765 → 1041, 1041-1, 1041-2, 1041-3
GI-8005766 → 1042, 1042-1, 1042-2, 1042-3
GI-8005767 → 1043, 1043-1, 1043-2, 1043-3
```

Two SAPs have stock but **no recipe line**: `GI-6002241/1052` (Solvent CF-CE),
`GI-6002245/1051`. They will never appear in a benchmark comparison.

### 1.4 `Manpower_Hour_Details.xlsx` — sheet `Productivity Estimation`

**This file has TWO unrelated blocks in one sheet, and the header is on row 4
(0-indexed row 3), not row 1.** Any reader that assumes `header=0` gets 24
`Unnamed:` columns.

**Block A — rows 5–30, the productivity benchmark (26 rows).**
Columns: `Activity Code#`, `Type` (CV/ME), `System`, `Lining_System_Code`,
`Activity`, `Execution_Sub_Activity_Code`, `Sub-Activity`, **nine role columns**
(`Blaster`, `Potman`, `Rubber Liner`, `Coating applicator`, `Sheet Preparator`,
`Mason`, `mortar mixer`, `brick cutter`, `Helper`), `Person/Crew`, `Hrs./shift`,
`Manhr. / Shift`, `Standard Productivity /Shift`, `SQ. Mtr/Hr./Person`, `Remarks`.

The arithmetic is **internally consistent on all 26 rows** — verified:

```
Person/Crew        = Σ(nine role columns)
Manhr. / Shift     = Person/Crew × Hrs./shift
SQ. Mtr/Hr./Person = Standard Productivity /Shift ÷ Manhr. / Shift
```

That means only two of the five numeric columns are independent. **Store the
inputs and derive the rest**, or a hand-edit will make the row lie about itself.

**Block B — rows 41–49, a per-area Day/Night crew deployment sample.** Three
areas (`SCRUBBER BUILDING - J`, `SULPHURIC ACID STORAGE AREA`, `FAN DUCT
SUPPORTING AREA TRAIN-J`), each `Day`/`Night`, by grade (Mason 4, mortar mixer 2,
brick cutter 1, Helper 2, **Crew size 9**), `Total Manpower` 27. Every Night
column is 0. This is a worked example, not a roster — see Q6.

---

## 2. TRACK 1 — Global system code migration (int → string)

### 2.1 The good news: the storage layer never needed the migration

`Lining_System_Code` is **already `Column(Text)`** everywhere:
`backend/models.py:871` (`sme_sqm_progress`), `:894` (`sme_equipment`), `:955`
(`sme_recipe`), `:983`. `sme_master.py` validates it as
`str = Field(min_length=1, max_length=40)`. Both SME engines coerce to string —
`sme_engine.py:164` uses `_s(...)`, `engine.ts:49` types it `string | number` and
runs it through `s()`. **No Alembic migration is required for the type itself.**

### 2.2 The bad news: two ingest paths reject the new codes, and one does it silently

> **This is the finding that gates the whole phase.**

```python
# backend/api/bulk_import.py:1293  — plan_sme_equipment
try:
    code = str(int(float(code)))
except (TypeError, ValueError):
    skipped_nonnum += 1          # ← 'LSC1' lands here
    continue
```

`int(float("LSC1"))` raises `ValueError`. Every one of the **292 equipment rows
is skipped**, and the run reports a *warning* — `skipped 292 row(s) with
non-numeric Lining_System_Code` — then completes **successfully with zero
equipment written**. `tools/pg_excel_sync.py` delegates its column planning to
exactly this function (locked rule 3: "the planners live in
`backend/api/bulk_import.py`"), so the everyday sync command is the one that
fails this way.

`bulk_import.py:1408` is the same cast in `plan_sme_recipes`. That one at least
lands in a `rejects` list rather than a warning — all 46 recipe rows rejected.

**Fix:** delete both casts and keep `_s()`'s stripped string. The comment says
the cast exists to drop `To_Be_Confirmed_LSC` placeholders — that intent is
preserved by an explicit `if code.startswith("To_Be_Confirmed")` skip, or by
validating against the recipe's own code set, neither of which rejects `LSC1`.

### 2.3 Silent misordering — two helpers that no longer sort anything

| Helper | Behaviour on `LSC1` | Effect |
|---|---|---|
| `sme.py:325` `_syskey` | `s.isdigit()` false → `(1, "LSC1")` | every code falls in bucket 1 → **lexical** order: `LSC1, LSC10, LSC11, LSC2, LSC3…` |
| `sme_export_layouts.py` `_code_sort_key` | non-digit → `9999` | **every code returns 9999** → the sort is a no-op; Excel blocks emerge in arbitrary order |

Neither raises. Both are wrong on screen and in the exported workbook.

**Fix:** one shared natural-sort key — split the alpha prefix from the numeric
tail, sort `(prefix, int(tail), suffix)`. `LSC2` then precedes `LSC10` as a human
expects. Because sorting is presentation, this belongs in **one** helper imported
by both call sites, not copied.

Plain `ORDER BY "Lining_System_Code"` at `sme.py:167, 218, 499, 1075` and
`manhours.py:141` is lexical for the same reason. Where the order is
user-visible, sort in Python with the shared key; where it only feeds a
`GROUP BY`, leave it.

### 2.4 The legacy stack will crash, and one crash eats the data

`legacy/` is frozen but still gated at **599/0**. These break on `LSC1`:

| Location | Failure |
|---|---|
| `legacy/scripts/sme_bootstrap.py:85, 197` | `.astype(float).astype(int)` → `ValueError` |
| `legacy/scripts/sme_bootstrap.py:189-193` | `pd.to_numeric(errors="coerce")` then **drops non-numeric rows** — silently discards all 292 |
| `legacy/pages_internal/material_estimator_portal.py:1633, 2926, 3675, 4439, 5008, 4675, 5325, 6285` | `key=lambda x: int(x)` → `ValueError` |
| `legacy/pages_internal/material_estimator_portal.py:7102` | `ORDER BY CAST(lining_system_code AS INTEGER)` → SQL cast error |

**Recommendation (needs your ruling — Q1):** do **not** port the legacy estimator.
It is frozen, the new stack has superseded it, and `tools/parity_check.py` is
already retired as a gate. Apply the minimum that keeps `legacy/bug_check.py` at
599/0 and leave the Streamlit estimator visibly broken for `LSC*` data, or
retire the page.

### 2.5 Track 1 summary

| Item | Verdict |
|---|---|
| DB column types | **No change needed** — already `Text` |
| Alembic migration for Track 1 alone | **None** |
| `bulk_import.py:1293`, `:1408` | **Must fix — blocking, silent** |
| `_syskey`, `_code_sort_key` | Must fix — silent misordering |
| Legacy estimator | Ruling required (Q1) |
| Both SME engines | Untouched — they already coerce. **If you change either, change both in one commit** and regenerate the golden. |

---

## 3. TRACK 2 — The new consumption workflow (SK → Supervisor → HOD)

### 3.1 What exists, and what the request inverts

There is already a Supervisor↔SK flow, and **it runs the other way**.
`backend/api/services/supervisor.py`: the Supervisor raises an SMR, the **SK
approves** it, and approval mirrors lines into `pending_issues` with
`Work_Type=SUPERVISOR_REQUEST`, which then flow to the HOD queue.

Track 2 asks for **SK enters → Supervisor enriches → HOD approves**. That is a
second, opposite-direction pipeline. Building it as a mode of the SMR tables
would put two flows with opposite approval semantics in one state machine.

**Recommendation:** a **new** chain, `sme_execution_entry`, that reuses
`pending_issues` only at the final HOD approval — the moment quantity actually
leaves. That keeps stock, FEFO, burn rate and the QC gate needing no exception,
which is the same negative property PPE was built for (locked: "PPE rides the
ordinary issue form").

### 3.2 The gates already binding at the SK's issue

Both QSEP gates fire at `stage_consumption` and `approve_smr`. A new SK entry
point **must call the same two assertions** or it becomes a third door into
issuance that is not gated:

* Material Test Certificate — fixed by **Logistics**
* QC approval (`services/quality.assert_qc_cleared`) — fixed by the **QC**

A refusal message that does not name *which* gate sends the SK to the wrong
person. `QcClearanceBanner` shows both at once and should be reused.

### 3.3 Proposed state machine

```
sk_draft ──▶ awaiting_supervisor ──▶ awaiting_hod ──▶ approved ──▶ committed
                    │                      │
                    └──── returned_to_sk ◀──┘   (either can push back)
```

* **SK** writes material lines (SAP + qty + lot) and picks `Equipment_Tag_No`.
  Nothing is deducted. Both QSEP gates evaluated here.
* **Supervisor** picks `Execution_Sub_Activity_Code`, enters actual SQM and
  actual manpower. Benchmarks resolve **on save**, and are **snapshotted onto the
  row** (§3.5). If either variance breaches the band, the two reason fields
  become mandatory.
* **HOD** may **edit** the Supervisor's SQM / manpower / ESC / reasons before
  approving. Every edit is an audit row with before→after; the Supervisor's
  original values are preserved in a separate column, never overwritten — an
  HOD edit that erases what the Supervisor claimed destroys the evidence the
  variance report exists to show.
* **Approval** stages into `pending_issues` and deducts.

### 3.4 The comparison, and where each benchmark comes from

| Side | Benchmark | Source |
|---|---|---|
| Material | `For_1_SQM × Actual_SQM` per `(LSC, ESC, Material_Code, SAP_Code)` | `sme_recipe` (+ new ESC column) |
| Manpower | `Actual_SQM ÷ SQ. Mtr/Hr./Person` = expected man-hours | new `sme_manpower_norm` |

The existing `sme_consumption_log` already computes `Expected_Qty` and
`Variance_Pct` this way for the workbook-import path — the formulas should be
shared, not re-derived.

### 3.5 Snapshot the benchmark onto the row — non-negotiable

`sme_recipe` and the new norm table are **mutable master data** (`/sme/master/*`
went live on cutover day). If the variance report recomputes from live master
data, editing a `For_1_SQM` value silently rewrites the history of every
approved entry, and the reasons the Supervisor typed stop matching the numbers
beside them. Store `Benchmark_Qty`, `Benchmark_Manhours` and the `For_1_SQM` /
`SQ_M_Per_Hr_Person` used, at the moment of Supervisor save.

### 3.6 RBAC

Sits under existing roles — `store_keeper` (0), `supervisor` (1), `hod` (2). Per
locked rule 14, every new route needs an explicit `read_roles`, every new page a
manifest entry, or `npm run test:nav` fails the build. Auditor 403s on all
mutations via `readonly.py` and **must not** be added to the allowlist — these
routes change stock.

---

## 4. TRACK 3 — Manpower planning & roster

### 4.1 Far more exists than the brief assumes

`backend/api/manhours.py` is a full man-hours portal, exact-locked `{hod, admin}`:
`mh_employees`, `mh_timesheets`, `mh_manhour_estimates`, `mh_production`,
`mh_variance_notes`, plus `/mh/forecast`, `/mh/variance`, `/mh/productivity`,
`/mh/scorecard`, `/mh/employee-timeline` and an attendance importer.

`mh_employees` **already has** `Designation` and `Worker_Type` (default `'OWN'`).
So "Role/Designation" and "GI vs Non-GI" are existing columns to populate and
constrain, not new ones.

### 4.2 The direct contradiction — work-hour rules

```python
# backend/api/manhours.py:54-55
MH_NORMAL_THRESHOLD_HOURS = 8.0
MH_DEFAULT_BREAK_MINS     = 60

# backend/api/manhours.py:119-120
normal = round(min(total, MH_NORMAL_THRESHOLD_HOURS), 2)
ot     = round(max(0.0, total - MH_NORMAL_THRESHOLD_HOURS), 2)
```

The shipped rule is **one flat 8-hour threshold for everybody**. You have
specified **GI = 9h incl. 1h lunch, Non-GI = 11h incl. 1h lunch**.

Note what "incl. 1 hr lunch" does to the arithmetic. `compute_mh_hours` already
*subtracts* the break: `net = (gross − break)/60`. So a GI worker on site 09:00→18:00
has gross 9h, net **8h**. If the 9 is the *attendance* figure, the OT threshold on
`total` is **8**, and today's constant is already correct for GI. If the 9 is
*worked* hours, the threshold is 9 and the paid day is 10. **These give different
overtime on every row** — Q3.

And the third number: `Manpower_Hour_Details.xlsx` states **`Hrs./shift` = 11**
on 25 of 26 rows and **12** on one (`ESC23`, Buffing). The benchmark's own shift
length matches neither 9 nor 8. Q4.

**Whatever is ruled, the change is to shipped hour math** — `mh_timesheets` rows
already carry stored `Normal_Hours` / `OT_Hours`. Changing the threshold does
**not** retro-fix them. There are 0 timesheet rows in the 2026-08-17 dump, so
today the backfill is free; that will not stay true.

### 4.3 What is genuinely missing

| Need | Status |
|---|---|
| `Designation`, `Worker_Type` (GI/Non-GI) | **exist** — need a constrained vocabulary + HOD-managed custom additions |
| **Shift (Day/Night)** | **missing** from both `mh_employees` and `mh_timesheets` |
| `Execution_Sub_Activity_Code` | **missing everywhere** — zero occurrences in the entire codebase |
| Manpower norms (crew × role × productivity) | **missing** — new table |
| Overtime-minimising planner | **missing** — `/mh/forecast` takes a flat `crew_size` + `hours_per_day`, no roles, no GI/Non-GI split |

`mh_manhour_estimates` and `mh_variance_notes` are both unique on
`(Site_ID, Equipment_Tag, System_Code)` — **no ESC dimension, and one reason per
tag+system**. Track 2 needs *two* reasons per entry. These keys must widen.

### 4.4 The planner

Inputs: Equipment, System Code, Deadline Hours. Then:

1. Remaining SQM per `(Tag, LSC)` from `sme_sqm_progress`.
2. For each ESC in that LSC: `required_manhours = SQM ÷ SQ_M_Per_Hr_Person`,
   and per-role headcount from the norm's role columns × crew multiples.
3. Available = active `mh_employees` at the site, by `Designation` and `Shift`.
4. Gap per role = required − available → **procure**.
5. Optimise: fill from Non-GI first *where the role permits*, because Non-GI
   absorbs 11h before overtime against GI's 9h.

Point 5 is a real optimisation with a real objective, and **it is
underspecified as written** — "prioritise Non-GI" and "reduce overtime" can
conflict with cost, and no cost data exists in any of the four files. Q5.

---

## 5. TRACK 4 — FEFO lot handling & expiry

### 5.1 The finding: FEFO is inert, because there is no lot data at all

From `.backups/gihub_2026-08-17_020932.sql.gz`:

| Table | Rows | Lot_Number populated | Expiry_Date populated |
|---|---|---|---|
| `lots` | **0** | — | — |
| `receipts` | 632 | **0** | **0** |
| `consumption` | 1,674 | **0** | — |

`SQL_LOT_BALANCE` (`backend/api/stock.py:96`) selects **`FROM lots l`**. With
`lots` empty, `_FEFO_PICK` (`services/ledger.py:73`) matches nothing and
`fefo_lot()` (`:226`) returns `None` on every call. Every issue is un-lotted;
`FEFO_Override` has never fired.

So Track 4 is not "enforce FEFO" — **the suggestion has nothing to suggest.**
The capture path has to exist first.

### 5.2 Expiry is TEXT, and FEFO sorts it as TEXT

`lots.Expiry_Date` is `Column(Text)`; `_FEFO_PICK` does
`ORDER BY "Expiry_Date" ASC`. That is correct **only** for zero-padded ISO
`YYYY-MM-DD`. One `13/08/2026` sorts before every `2026-*` and gets picked first,
forever, silently.

There is no evidence either way in production — the column is 100% NULL — but
`receipts."Date"` in the same dump is stored as `2026-04-26 00:00:00`, so the
codebase does not consistently write bare ISO dates. **Recommendation:** store
`Expiry_Date` as a real `DATE`, or add a `CHECK (Expiry_Date ~ '^\d{4}-\d{2}-\d{2}$')`.
A `DATE` column is the honest fix and the table is empty, so the migration is free
today.

### 5.3 The SK's lot field is free text

`frontend/src/pages/IssuePage.tsx:412` renders `Lot_Number` as a plain
`<Input placeholder="blank → FEFO auto-pick" />`. There is no list of lots, no
expiry shown, no indication of which lot FEFO *would* pick. A typo becomes a
`Lot_Number` that matches no lot and is stored anyway.

**Proposed:** replace with a `<Select>` fed by a new
`GET /stock/lots?sap=&site=` returning open lots with `Expiry_Date`,
`Remaining_Qty` and a `fefo_rank`. Rank 1 pre-selected and badged **FEFO**;
picking anything else keeps the existing mandatory-reason behaviour
(`IssuePage.tsx:427-431`).

### 5.4 What must not change

FEFO stays **allow-and-log, never a hard block** — locked 2026-06-30 and
re-affirmed by QSEP: "`assert_qc_cleared` is about QUALITY STATUS on 36 SAPs,
while FEFO and over-issue stay allow-and-log on everything. Never implement one
by promoting the other's warning to an error." A dropdown that *cannot* express
"none of these" would implement the block by omission — keep a free-text escape.

---

## 6. TRACK 5 — `./bin/dev.sh localhost` → `PG_FORMULA: unbound variable`

**I could not reproduce this against `main`, and I believe the working tree is
already correct.**

* `PG_FORMULA="postgresql@16"` is set at **`bin/dev.sh:36`**, top level, before
  every function.
* Line 127 is `info "…starting $PG_FORMULA…"` inside `ensure_postgres()`.
* `git status` shows `bin/` clean; `git log -S` shows the assignment present
  since the file was introduced (`df8fe84`).
* There is no `unset`, no `env -i`, no re-exec, and no `local PG_FORMULA`.
* Postgres was **down** during this session — the exact condition that reaches
  line 127 (the early `return 0` is skipped) — and I ran the real, unmodified
  script under both `/bin/bash` and `env bash` (both 3.2.57 here) with PATH
  stubs forcing `pg_isready` to fail. Both printed
  `▸ Postgres not answering on :5433 — starting postgresql@16…` and resolved
  the variable correctly.

The failure path is only reachable when Postgres is down, which is why it looks
intermittent. My best guess is that the error came from a checkout or shell that
predates the current file.

**Proposed, regardless** — two cheap changes that cost nothing and make the class
of bug impossible:

1. `PG_FORMULA="${PG_FORMULA:-postgresql@16}"` in **both** `bin/dev.sh:36` and
   `bin/power.sh:39` — same fix, both files, since they duplicate the constant.
2. `bin/dev.sh:239` is `[ "$env" = "tunnel" ] && assert_no_foreign_connector`.
   Under `set -e` an AND-list whose final status is 1 is an abort risk; make it
   `if [ … ]; then … ; fi`. Unrelated to your error, but it is the next one.

**Please paste the exact terminal output and `git rev-parse HEAD` from the run
that failed** (Q7) — if it reproduces at `0cd0ac3` I want the real cause, not a
defensive default papering over it.

---

## 7. Schema changes

### 7.1 The two that are blocking and silent

#### (a) `sme_recipe` — the unique key is violated by the new workbook

`backend/models.py:976` — `UniqueConstraint("Lining_System_Code", "Material_Code", "SAP_Code")`.

Against `For_1_SQM.xlsx`:

| Key | Distinct | Duplicates |
|---|---|---|
| `(LSC, Material_Code, SAP_Code)` | 44 | **2** |
| `(LSC, ESC, Material_Code, SAP_Code)` | **46** | **0** |

The two collisions:

```
LSC2 · GI-6002243 · 1049  →  ESC21 Primer 0.2700  +  ESC22 Screed 1.4674  (merged 1.7374)
LSC2 · GI-6002244 · 1050  →  ESC21 Primer 0.1350  +  ESC22 Screed 0.7326  (merged 0.8676)
```

And `plan_sme_recipes` does not reject that collision — for a SAP-aware file it
**sums** it as a deliberate multi-coat line (`cur["For_1_SQM"] += qty`,
`coat_merges += 1`). That was *right* when ESC did not exist: one system consumes
1.7374 kg/m² of Resin A across all coats. It is **wrong** the moment a Supervisor
picks ESC21 and the system compares a correct primer draw against 1.7374 instead
of 0.2700: the entry reads as **15.5 % of benchmark — an apparent 84.5 %
under-consumption — on every primer entry**, and the mirror error understates
every screed entry by 15.5 points. Both would demand a written justification for
a variance that does not exist.

So `ESC` is **not a new attribute — it splits an existing merged number.** The
key must widen to `(Lining_System_Code, Execution_Sub_Activity_Code,
Material_Code, SAP_Code)`, and `coat_merges` must only fire for a repeat of the
*full* key. `sme_recipe` holds 41 rows against the workbook's 46; the delta is
these merges plus `LSC11`.

Verified: no `(LSC, MC, SAP)` triple spans more than those two ESCs, so the
widening is a clean split with no ambiguity about which row gets which quantity.

#### (b) `bulk_import.py:1293` / `:1408` — see §2.2

### 7.2 New tables

```
sme_manpower_norm                    -- Manpower_Hour_Details block A
  id PK
  Type                    text NOT NULL      -- 'CV' | 'ME'
  Lining_System_Code      text NOT NULL
  Execution_Sub_Activity_Code text NOT NULL
  Activity, Sub_Activity  text
  Variant_Key             text NOT NULL      -- disambiguates the collisions, §9.2
  Person_Crew             int  NOT NULL      -- = Σ role rows (derived, stored)
  Hrs_Per_Shift           numeric NOT NULL
  Std_Productivity_Shift  numeric NOT NULL   -- the only free productivity input
  Remarks                 text
  UNIQUE (Type, Lining_System_Code, Execution_Sub_Activity_Code, Variant_Key)
  -- Manhr_Shift and SQ_M_Per_Hr_Person are DERIVED, never stored twice (§1.4)

sme_manpower_norm_role               -- one row per role, not nine columns
  norm_id FK → sme_manpower_norm
  Role_Code   text NOT NULL
  Headcount   int  NOT NULL
  UNIQUE (norm_id, Role_Code)

mh_roles                             -- the dropdown; seeded from the 9 file roles
  Role_Code PK, Label, is_custom bool, created_by, Site_ID NULL
                                     -- HOD custom additions, per rule 14 RBAC

sme_execution_entry                  -- the Track 2 header
  id PK, Site_ID, Entry_Date, Equipment_Tag_No, Lining_System_Code,
  Execution_Sub_Activity_Code,       -- NULL until the Supervisor sets it
  status text NOT NULL,              -- sk_draft | awaiting_supervisor |
                                     -- awaiting_hod | approved | committed |
                                     -- returned_to_sk | rejected
  Actual_SQM numeric, Actual_Manhours numeric,
  Benchmark_Qty numeric, Benchmark_Manhours numeric,   -- SNAPSHOT (§3.5)
  Material_Variance_Pct numeric, Manpower_Variance_Pct numeric,
  Material_Variance_Reason text, Manpower_Variance_Reason text,
  sk_username, supervisor_username, hod_username,
  supervisor_submitted_at, hod_approved_at,
  supervisor_original_json jsonb,    -- preserved across HOD edits (§3.3)
  staged_pi_id int                   -- → pending_issues at approval

sme_execution_entry_line             -- the SK's material lines
  entry_id FK, SAP_Code, Material_Code, Quantity, Lot_Number, FEFO_Override
  UNIQUE (entry_id, SAP_Code, Lot_Number)
```

### 7.3 Altered

| Table | Change | Why |
|---|---|---|
| `sme_recipe` | **+ `Execution_Sub_Activity_Code`**, widen UQ | §7.1(a) — blocking |
| `mh_employees` | **+ `Shift`** (`Day`/`Night`) | §4.3 |
| `mh_employees` | `Worker_Type` → constrained `GI`/`NON_GI`; migrate `'OWN'` | Q2 |
| `mh_timesheets` | **+ `Shift`**, **+ `Execution_Sub_Activity_Code`** | §4.3 |
| `mh_manhour_estimates` | + ESC; widen UQ to `(Site, Tag, System, ESC)` | §4.3 |
| `mh_variance_notes` | + ESC + **second reason column**; widen UQ | §4.3 — Track 2 needs two reasons |
| `lots.Expiry_Date` | `Text` → `Date` (table is empty) | §5.2 |

### 7.4 Migration ordering

One Alembic revision on `c7a93e5d2b18`, in this order — the recipe backfill must
land **before** any Track 2 code reads a benchmark:

1. Add `sme_recipe.Execution_Sub_Activity_Code` nullable.
2. **Backfill from `For_1_SQM.xlsx`**, splitting the two merged LSC2 rows back
   into their primer and screed quantities.
3. Assert no NULLs remain, set `NOT NULL`, swap the unique constraint.
4. Everything else in §7.2 / §7.3.

> ⚠️ **The known cutover gap applies here.** `tools/migration/cutover_migrate.py`
> **stamps** Alembic to head without running it, so a fresh production cut would
> skip step 2 entirely and ship a `NOT NULL` column with no data behind it. This
> is the open gap already recorded under *FUTURE* in `PROJECT_HANDOVER.md`, and
> Phase 7 is the first migration with a **data** step that cannot be skipped.
> **It must be closed before deployment, not after.**

---

## 8. Implementation order

Each phase ends green on every gate in §11. Phases 1–2 are prerequisites for
everything else — no UI work should start before they land.

| # | Phase | Contains | Risk |
|---|---|---|---|
| **1** | **Unblock ingest** | remove both `int(float(code))` casts; natural-sort helper for `_syskey` + `_code_sort_key`; legacy ruling (Q1); `dev.sh` hardening | **HIGH** — silent data loss today |
| **2** | **ESC in the recipe** | migration §7.4 steps 1-3; `plan_sme_recipes` ESC-aware; `coat_merges` on the full key; re-sync all four workbooks; close the cutover data-step gap | **HIGH** — changes the meaning of stored numbers |
| **3** | **Manpower master data** | `sme_manpower_norm` + `_role` + `mh_roles`; two-block importer for `Manpower_Hour_Details.xlsx`; SME Master Data tab | MED |
| **4** | **Roster extension** | `Shift`; `Worker_Type` GI/Non-GI; Designation from `mh_roles`; Manpower tab | MED — hour-rule ruling (Q3/Q4) lands here |
| **5** | **Track 2 workflow** | `sme_execution_entry` chain, SK/Supervisor/HOD screens, QSEP gates re-used, benchmark snapshot, HOD edit + audit | **HIGH** — touches issuance |
| **6** | **Variance reporting** | material + manpower vs benchmark, mandatory reasons, HOD review view, exports (rule 12 `_defuse`) | MED |
| **7** | **Manpower planner** | requirement vs roster vs procure, by role, overtime optimisation | MED — needs Q5 |
| **8** | **FEFO lots** | lot capture at receipt; `Expiry_Date` → `DATE`; `GET /stock/lots`; `IssuePage` Select + FEFO badge | MED |

---

## 9. Edge cases and contradictions

### 9.1 The Manpower file's `Lining_System_Code` column contains ESC values

Four Blasting rows (`Activity Code#` 1, 12, 19, 23) carry **`ESC1` / `ESC2`** in
`Lining_System_Code`. That is not an LSC. Blasting is system-agnostic — it is
surface preparation done before any lining — so there was no LSC to write and
the ESC was put in the slot.

A naive FK from `sme_manpower_norm.Lining_System_Code` → `sme_recipe` rejects
these four rows, and they are exactly the manpower-only rows the brief calls out.
**Proposal:** normalise them to `NULL` with an explicit
`is_system_agnostic bool`, so "applies to every system" is stated rather than
encoded as a bad value. Q8.

### 9.2 `(Type, LSC, ESC)` is **not** unique in the Manpower file

| Key | Rows | Values |
|---|---|---|
| `CV · ESC1 · ESC1` (Blasting) | **3** | crew 4 / prod 300 / 6.82 · crew 2 / 40 / 1.82 · crew 2 / 40 / 1.82 |
| `CV · LSC10 · ESC101` (PU Seal Coat 1mm) | **2** | prod **70** (parent `PU lining 4mm`) · prod **90** (parent `PU lining 6mm`) |

The second is the harder one: **`LSC10` Seal Coat is shared between the LSC8 (4mm)
and LSC9 (6mm) systems, and its productivity depends on the parent.** The row
cannot be identified without knowing which system it was reached from.

Three blasting rows with productivity differing 7.5× (300 vs 40) is not a
variant — it looks like **three different blasting contexts** that the file does
not name. Q9.

Without a ruling, any importer must either drop rows or pick one arbitrarily.
`Variant_Key` in §7.2 is a placeholder for whatever you decide identifies them.

`ESC41` and `ESC51` also appear twice each, split by `Type` CV/ME — but with
**identical** numbers. Harmless today; they will diverge, so `Type` belongs in
the key regardless.

### 9.3 Manpower-only sub-activities — the brief is confirmed

Three ESCs have a manpower norm and **no** material recipe: **`ESC1`, `ESC2`
(Blasting), `ESC23` (Buffing on coating)**. The reverse set is **empty** — every
material ESC has a manpower row.

Consequences for Track 2:
* A Supervisor picking `ESC1` must get a form with **no material comparison at
  all** — not a zero benchmark. Zero benchmark against zero actual is a 0/0
  variance, and against any actual it is an infinite one.
* The SK step must be **skippable**. A blasting entry has no Surface Shields, so
  requiring the SK to start the chain makes manpower-only work unrecordable.
  **This inverts the stated flow for these three ESCs** — the Supervisor must be
  able to open an entry directly. Q10.
* Both QSEP gates are material gates. A manpower-only entry has no SAP to check;
  it must bypass them **by having no lines**, not by an exemption flag.

### 9.4 UOM disagrees between the two workbooks on 26 of 31 material pairs

`For_1_SQM.UOM` vs `Materials_DetailsAvailable_Qty.UOM`:

```
KG↔EA · KG↔DR · KG↔BAG · KG↔Can · SQM↔M2 · NOS↔NO · Bottle↔EA
```

`PACKAGE SIZE` agrees on every row, so these are the same physical thing named
twice: the recipe speaks in **base units** (KG), the warehouse in **issue units**
(Can, BAG, DR, EA).

`SQM↔M2` and `NOS↔NO` are pure spelling. The rest are **not** — comparing an
actual issued in `Can` against a benchmark in `KG` is a unit error that produces
a plausible-looking variance. The locked P1 design ("base-UoM + entry conversion")
already anticipates this; Phase 7 must route the comparison through it and
**refuse to compare** when no conversion exists, rather than compare raw numbers.

### 9.5 `LSC11` has a recipe but no equipment

`For_1_SQM` and the Manpower file both define `LSC11` (Carbon Brick lining 75mm,
`ESC111`). No row in `Equipment.xlsx` uses it. Harmless — it will appear in
master data and never in a demand calculation — but a "systems with no equipment"
report is worth having so it is a known absence rather than a suspected bug.

### 9.6 33 equipment rows have no `Equipment_Tag_No.`

All are BROWN FIELD areas identified only by `Name` + `Sl. #` (`Train Unloading
MGA Vessel PIT`, `Existing MGA Pump Area`). `bulk_import.py:1283-1284` already
backfills the tag from `Name`, so they *do* land — as equipment whose "tag" is a
sentence. In the SK's Equipment dropdown that is at best ugly and at worst
ambiguous: two different areas share the name `Existing MGA Pump Area` across
different `Sl. #`. Q11.

### 9.7 `WBS #` is entirely empty

0 of 292 rows. There is a locked UAT-5 ruling on WBS casing and preservation,
and `bug_check.py`'s models-parity check encodes it. An all-empty column will
not break that, but nothing in Phase 7 should start *depending* on WBS.

### 9.8 Two SAPs have stock and no benchmark

`GI-6002241/1052` and `GI-6002245/1051` exist in the Materials file with no
`For_1_SQM` line. If an SK issues them against an execution entry, there is no
benchmark to compare. They must be **reportable as "issued, not benchmarked"**
rather than silently excluded from the variance total — an exclusion is how
consumption disappears from a report that is supposed to be complete.

### 9.9 The derived-column trap in the Manpower file

`Person/Crew`, `Manhr. / Shift` and `SQ. Mtr/Hr./Person` are all derived (§1.4).
Storing all five as editable fields means a Master Data edit can produce a row
whose crew does not equal its roles and whose productivity does not match its
man-hours. Store `Std_Productivity_Shift`, `Hrs_Per_Shift` and the role rows;
derive the other three.

### 9.10 Rule 1a still binds

Nothing in Track 2 may net actual consumption off `sme_inventory_seed`.
`sme_actuals.py` is the precedent — it is greppped by suite BJ, which fails if
the seed table so much as reappears in that module. The new execution entry sits
beside the plan, exactly as the Actual Physical Balance does.

### 9.11 `sme_actuals.py` is `hod`-locked, and Track 2 is not

`/sme/actuals/*` is exact-locked `{hod}` because assigning consumption to
equipment is master-data-grade work. Track 2 gives the **Supervisor** that same
assignment power (ESC + SQM). That is a deliberate widening of who may bind
consumption to equipment. It is defensible — the Supervisor is the person who
was there — but it should be a stated ruling, not a side effect. Q12.

---

## 10. Clarifying questions

Ordered by how much they block. **Q1–Q4 block Phase 1–2; the rest can be answered
as their phase comes up.**

**Q1 — Legacy estimator.** Do we (a) leave `legacy/` broken for `LSC*` and keep
`bug_check.py` at 599/0, (b) fix its ~10 int-casts too, or (c) retire the legacy
estimator page? *Recommendation: (a).*

**Q2 — `Worker_Type`.** Today `mh_employees.Worker_Type` defaults to `'OWN'` and
22 rows exist. Is `OWN` == `GI`? Should I migrate `OWN → GI` and add `NON_GI`, or
is GI/Non-GI a *separate* axis from the existing values?

**Q3 — The 9 and the 11.** Are they **attendance** hours (gross, lunch included —
so net worked is 8 and 10) or **worked** hours (so the paid day is 10 and 12)?
`compute_mh_hours` already subtracts the break, so this changes the OT on every
row.

**Q4 — Which shift length wins?** The roster rule says 9/11. The benchmark file
says `Hrs./shift` = 11 (and 12 for `ESC23`). When the planner converts required
man-hours into shifts, does it use the roster rule or the benchmark's own figure?

**Q5 — Optimisation objective.** "Reduce overtime by prioritising Non-GI" — is
the objective (a) minimise total OT hours, (b) minimise cost, or (c) minimise
GI OT specifically? There is **no cost data in any of the four files**, so (b)
needs a rate table we do not have.

**Q6 — Block B.** Is the Day/Night deployment table (rows 41-49) reference data
to import, or a worked example to ignore? Every Night column is 0, which reads
like an illustration rather than a live roster.

**Q7 — `dev.sh`.** Please paste the exact terminal output and
`git rev-parse HEAD` from the failing run (§6).

**Q8 — Blasting's `Lining_System_Code`.** Confirm `ESC1`/`ESC2` in that column is
a data-entry artefact and should be normalised to "applies to all systems", not
preserved literally.

**Q9 — The duplicate norms.** What distinguishes the **three** `CV·ESC1` blasting
rows (productivity 300 vs 40 vs 40) and the **two** `LSC10·ESC101` seal-coat rows
(70 vs 90)? For the seal coat I can key on the parent system (LSC8 vs LSC9) if you
confirm that is the intent. For blasting I have nothing to key on.

**Q10 — Manpower-only entries.** For `ESC1`/`ESC2`/`ESC23`, may the **Supervisor
open an entry directly**, bypassing the SK step? The stated flow starts at the
SK, but those activities consume no Surface Shields.

**Q11 — The 33 tagless areas.** Should the Equipment picker show them by `Name`
(current backfill behaviour), by a synthesised tag, or should the operator add
real tags to `Equipment.xlsx`? *Recommendation: add tags to the workbook — it is
the only fix that survives a re-sync.*

**Q12 — Supervisor scope.** Confirm the Supervisor may bind consumption to
equipment (ESC + SQM), which today is `hod`-only via `/sme/actuals`.

**Q13 — Variance band.** At what deviation do the two reason boxes become
mandatory — any non-zero difference, or a tolerance (±5 %? ±10 %)? "Higher/lower
than benchmark" taken literally makes them mandatory on essentially every entry,
because an exact match is vanishingly rare.

**Q14 — HOD edits.** When the HOD edits the Supervisor's values, is the
Supervisor notified, and does the entry require re-justification if the HOD's
edit *creates* a variance that was not there before?

---

## 11. Gate impact

Phase 7 touches the SME master data, so the whole locked set must stay green
(baselines from `SESSION_HANDOVER.md` §4):

| Gate | Baseline | Expected Phase 7 impact |
|---|---|---|
| Backend service tests | **1502 / 0** | grows — new suites for ESC keying, the workflow state machine, hour rules, FEFO lot picking |
| Playwright E2E | **90 / 90** | grows — SK→Supervisor→HOD walk-through |
| SME UI math | **33 / 0** | must hold |
| SME TS↔PY parity | **1,313 comparisons** | must hold; **if either engine changes, both change in one commit** + regenerate the golden |
| Legacy regression | **599 / 0** | at risk — see Q1 |
| Navigation route coverage | **46 routes, all claimed** | grows; fails the build if a new page has no manifest entry (rule 14) |
| Frontend | `tsc -b` + build + `oxlint` clean | must hold |
| Alembic | single head `c7a93e5d2b18` | **one** new revision, single head preserved |

Per rule 13, `MANUAL_TESTING_GUIDE.md` is updated in the same PR as each phase —
Definition of Done, not a follow-up. Per rule 12, every new export routes through
`_defuse`, and `_defuse` never touches a number.

---

## 12. What I need from you

Answer **Q1–Q4** and I can start Phase 1 immediately — it is self-contained,
unblocks the workbook sync, and fixes live silent data loss. **Q9 blocks Phase 3**
and is the one I cannot resolve from the data.
