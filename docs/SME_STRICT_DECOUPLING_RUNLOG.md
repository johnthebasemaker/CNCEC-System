# SME ⇄ ERP Strict Decoupling — run log

**Date:** 2026-08-02 · **Branch:** `feat/sme-strict-decoupling`

> **The rule.** The main ERP inventory's live ledger movements — consumptions and
> receipts — must not affect the SME module. They are two entirely different
> pools of data and are calculated completely separately. The SME estimator's
> stock numbers rely solely on its own isolated tables (the data ingested from
> the Excel workbooks).

---

## 1. Where the ledger was bleeding in

An audit of `backend/api/sme.py`, `backend/models.py`, `backend/api/sme_engine.py`
and `tools/pg_excel_sync.py` found **exactly two** leaks, plus one consequence.

### 1.1 `SQL_SME_MATERIALS` — the derived availability view

```sql
-- before
(s."Initial_Available_Qty" + Σreceipts − Σconsumption) AS available_qty
--   …where each Σ joined the ledger on the component's own SAP,
--     gated on that SAP existing in the ERP `inventory` master.
```

Every SME surface drank from this: `/sme/materials`, `/sme/model-snapshot` (and so
the browser engine), `/sme/demand-matrix`, `/sme/plan/cascade`, every export, and
`sme_master.list_materials`.

```sql
-- after
s."Initial_Available_Qty" AS available_qty,
s."Initial_Ordered_Qty"   AS ordered_qty
FROM sme_inventory_seed s
```

The `received_qty` / `consumed_qty` columns went with it. They were pure ERP
readings; leaving them in the payload would have invited exactly the recoupling
the rule forbids.

### 1.2 `_CALC_POOL_SQL` — the Smart Calculator's stock pool

Its **entire** notion of stock was `Σreceipts − Σconsumption − Σreturns`, gated on
the SAP existing in ERP `inventory`. It now pools
`sme_inventory_seed.Initial_Available_Qty` over the variant SAPs sharing a
`Material_Code` — same pooling semantics, same `NULL`-means-unknown contract, SME
source.

### 1.3 The consequence: effective-ordered netting had to go

`sme_engine.build_model` / `engine.ts buildModel` computed

```
effective_ordered = max(Initial_Ordered_Qty − Σreceipts, 0)      # ruling Q2a
```

That netting existed **only** because arriving goods inflated `available_qty`
through the ledger, so counting the order as well double-counted them. With the
ledger disconnected nothing inflates availability — subtracting receipts would now
strip units from the order that were never added. `GI-8005762` would lose the
10,920 it has received against an order of 95,200.

Both engines now take `ordered_qty` at face value, clamped at zero (a workbook cell
can be negative). Changed in **one commit, both languages**, per the standing rule.

### What was *not* touched

* `sme_consumption_log` is an **SME-owned** table (production entries against SQM),
  not the ERP ledger. It never fed `available_qty` before and does not now.
  Wiring it in would be a new feature, not a decoupling.
* The Smart Calculator still pools per `Material_Code` across variant SAPs
  (2026-07-18 ruling). Only the *source* changed. Worth a separate look: that
  pooling is in tension with the `(Material_Code, SAP_Code)` component-identity
  rule, but it predates this work and is out of scope here.
* The frozen legacy SQLite `sme_materials_view` in `backend/models.py` is
  unchanged — it is the frozen legacy artefact.

---

## 2. How it is pinned — suite BA (11 checks)

A grep of the SQL alone would pass while a join hid behind an ORM call, so BA is a
**before/after**: seed an SME material, take every SME read, post real ERP movement
against the very SAP that material uses, take them again.

| Check | What it proves |
|---|---|
| baseline | 100 available / 60 on order read straight off the seed row |
| payload shape | no `received_qty` / `consumed_qty` in `/sme/materials` **or** the model snapshot |
| +500 received, −70 consumed, +30 returned | availability stays **100** (it was `100 + 500 − 70 = 530` before) |
| dirty-SAP receipt (`SVCBA - 1`, 900) | misses just as completely — the old join normalised it onto the same component |
| on-order pool | stays 60, not `60 − 500` clamped to 0 |
| Smart Calculator | 100 before and after; shortfall unchanged |
| **byte-identical** | the whole probe dict is `==` across the movement |
| seed edit → 250 | decoupled is not frozen: every SME read moves at once |
| source guard | neither query so much as *names* an ERP table (word-boundary matched, so `sme_inventory_seed` cannot trip it) |

Elsewhere:

* **Parity fixture** keeps its `received_qty` values (M1 = 40, M2 = 15, M6 = 25) as
  **poison pills**. The golden proves both engines *ignore* the field rather than
  merely proving it is absent from the payload. Re-read it and M1's ordered pool
  moves 100 → 60 and the gate fails on both sides at once.
* **Suite AN** (Smart Calculator) grew a 500-unit ERP receipt against `SVCN-1` that
  the calculator must not see — it reads 150 from the seed, not 650 from the ledger.
* **Suite AZ**'s `az-sql` block was inverted: it now proves `available_qty` **is**
  `Initial_Available_Qty` per component.
* **`_ROLLUP_COLS`** (the derived-view parity gate) dropped the two ledger columns.
  Its `available_qty` comparison now additionally asserts the frozen dataset carries
  no ERP movement against an SME material — true on a fresh cutover, which is the
  only state `parity_check.py` is meaningful in anyway.

---

## 3. `tools/pg_excel_sync.py` — future-proofing the CLI

**SME by default, ERP opt-in.** The command in the brief —

```bash
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
.venv/bin/python tools/pg_excel_sync.py --site CNCEC [--commit]
```

— used to run **all five kinds**, two of which write the live ERP warehouse master
and append to its ledger. It now runs the three SME kinds only and says so:

```
scope  : SME tables only — the ERP warehouse is untouched
```

`--erp` (or naming `inventory` / `ledger` in `--kinds`) widens it back and prints
`⚠️  ERP + SME — this run WRITES the live warehouse`. Workbooks are read lazily, so
a missing `CNCEC_Inventory.xlsx` no longer blocks an SME-only run. Four new checks
in suite AW pin all of that.

### The header bug the dry-run caught

`bulk_import._col` matches header names **exactly** (case-insensitively); it does
not normalise spaces vs underscores. The live `Materials_DetailsAvailable_Qty.xlsx`
ships **`Available Qty`** (space) beside `Ordered_Qty` (underscore), and only the
underscored alias was listed. The column resolved to `None`, `_f(None) or 0.0`
summed to zero, and the first dry-run proposed this for all 30 materials:

```
GI-6000012  1045  {'Initial_Available_Qty': 0.0, 'Initial_Ordered_Qty': 809.0}
GI-8005762  1036  {'Initial_Available_Qty': 0.0, 'Initial_Ordered_Qty': 95200.0}
…
```

Two fixes:

1. **Aliases** — `Available Qty` / `Available Quantity` / `Available` (and the
   `Ordered` equivalents) are listed alongside the underscored names.
2. **A missing quantity column no longer re-baselines to zero.** The field is
   dropped from the plan entirely, so the upsert's `COALESCE(excluded, table)`
   keeps the stored value and the diff can never propose a change for it — and the
   run prints a warning naming the headers it *did* find.

Two `az-header` checks pin both.

---

## 4. The sync that was run

Dry-run, then commit, then a third run to prove idempotency.

| Kind | Workbook | Result |
|---|---|---|
| `sme-recipes` | `For_1_SQM.xlsx` | +0 new ~0 changed =41 unchanged |
| `sme-equipment` | `Equipment.xlsx` | +0 new **~58 changed** =27 unchanged |
| `sme-materials` | `Materials_DetailsAvailable_Qty.xlsx` | +0 new **~30 changed** =2 unchanged |

0 rejects throughout; re-running is a no-op (`=41 / =85 / =32 unchanged`).
`gi_database.db` sha256 `00652932…ba038` **unchanged** before and after.

Post-sync `sme_inventory_seed`: **32 rows · 358,684 available · 483,196 on order**.

### Effect on the headline figures

| | Before | After |
|---|---|---|
| Remaining SQM | 49,435 | **42,403** |
| SQM achievable **now** | 2,778 (5.6%) | **3,312 (7.8%)** |
| SQM achievable **with on-order** | 24,743 (50.1%) | **12,430 (29.3%)** |

"With on-order" falling by half is the decoupling working as specified: the old
number counted a full `Initial_Ordered_Qty` *on top of* ERP receipts that had
already inflated availability. It is now the workbook's own two columns and
nothing else.

---

## 5. Gates

| Gate | Result |
|---|---|
| Backend service tests | **978 / 0** (was 963; +11 BA, +2 az-header, +2 net elsewhere) |
| SME TS↔PY parity | **1,258 comparisons** ✅ (golden regenerated) |
| Playwright E2E | **49 / 49** |
| Legacy regression | **599 / 0** |
| `tsc -b` + `vite build` + `oxlint` | ✅ |
| `gi_database.db` | sha256 unchanged |

---

## 6. Open items for the operator

### 6.1 `/analytics/lining-coverage` — the one remaining ERP↔SME meeting point

`backend/api/lining_analytics.py` (HOD page **Lining Coverage**,
`frontend/src/pages/LiningCoveragePage.tsx`, roles `hod` / `logistics`) runs the
same SME planning engine but **deliberately** swaps the availability pool to live
ledger stock (`Σreceipts − Σconsumption − Σreturns`, plus a 90-day burn rate →
days-of-cover → depletion date). It was left working, and here is the reasoning:

* It is **not the estimator**. Different router, different page, different roles.
  `/sme/*` is fully decoupled and suite BA proves it.
* The swap happens in a **local copy** of the snapshot rows. Nothing is written;
  the `sme_*` tables and `/sme/*` never see those figures.
* Answering *"what can the warehouse actually support today"* is the page's entire
  reason to exist. Fed from the seed it would compute exactly what `/sme/*`
  already computes, so the honest way to extend the rule here is to **retire the
  page**, not to re-source it.

Retiring it is an operator ruling, not a refactor — say the word and it goes.

### 6.2 ERP cannot express component identity

`PROJECT_HANDOVER.md` caveat 2 is now sharper, not resolved: the ERP `inventory`
table has a UNIQUE on `Material_Code`, so the variant SAPs of a multi-part system
cannot all carry it there. That is an **ERP-side** modelling gap and it no longer
touches the estimator at all — but it still means the ERP cannot express component
identity. It wants its own decision.

### 6.3 The Smart Calculator still pools per `Material_Code`

Only its *source* changed (ledger → seed). Pooling four component drums into one
stock figure is in tension with the `(Material_Code, SAP_Code)` component-identity
rule, but it is a 2026-07-18 ruling that predates this work and was out of scope.
