# SME "Ordered is the total, Available is a subset of it" — run log

**Date:** 2026-08-05 · **Branch:** `fix/sme-ordered-subset-rule`

> **The correction.** In `Materials_DetailsAvailable_Qty.xlsx`, `Ordered_Qty`
> does NOT mean "additional incoming stock". It is the **TOTAL amount procured
> for the entire project**, and `Available_Qty` is the portion of that order
> which has physically **ARRIVED**. Available is a **SUBSET** of Ordered.

The engine treated them as two independent buckets and added them. That
double-counted every unit already on the shelf.

---

## 1. Verified against the data before touching anything

### The subset rule holds on every row

All 32 rows of `Materials_DetailsAvailable_Qty.xlsx`: `Available_Qty ≤
Ordered_Qty`, **zero violations**. And **14 of the 32 are fully delivered** —
`Available == Ordered` — which is exactly where the additive reading doubled
the stock.

### The double-count, measured against the dashboard export

Reconciling `dashboard_material_balance.xlsx` (current output) against the
workbook, `Net Shortfall` was computed as `demand − available − ordered`; the
subset rule requires `demand − max(ordered, available)`.

| | |
|---|---|
| report rows with an **understated buy list** | **22 of 30** |
| total net shortfall as reported | **106,299.64** |
| total net shortfall, correct | **129,250.59** |
| **we would have under-ordered by** | **22,950.95 units** |

The understatement per material is *exactly* its `Available_Qty` — the
signature of counting arrived stock twice.

### The worst case, and why it mattered

`GI-8005763` — **143,000 arrived of 143,000 ordered** (fully delivered), demand
**152,685**.

```
additive :  143,000 + 143,000 = 286,000  ⇒  nothing to buy   ❌
subset   :  max(143,000, 143,000)        ⇒  9,685 to buy     ✔
```

A **9,685-unit shortage on a headline material was completely invisible**, and
the material did not appear on the buy list at all.

---

## 2. The fix — one line of maths, in both engines

```python
# before
pool_ordered_init[mat] = ordered
# after
pending = ordered - available
pool_pending_init[mat] = pending if pending > 0 else 0
```

Everything else in the contract falls out of the existing cascade arithmetic:

```
tier 1  = available
tier 2  = max(ordered − available, 0)          the PENDING DELIVERY
ceiling = tier1 + tier2 = max(available, ordered)   ← Allocated_Qty
to buy  = demand − Allocated_Qty
        = max(demand − max(available, ordered), 0)  ← Shortfall_Qty
```

Both engines changed in the same commit; golden regenerated; **parity 1,313
comparisons**.

### Field renames — because a name that lies is how this class of bug spreads

`Alloc_Ordered` no longer means "allocated from the order"; it means "allocated
from the part of the order that has not arrived". The names now say so:

| was | is |
|---|---|
| `pool_ordered_init` / `poolOrderedInit` | `pool_pending_init` / `poolPendingInit` |
| `Alloc_Ordered` | `Alloc_Pending` |
| `Ordered_Pool_Before` / `_After` | `Pending_Pool_Before` / `_After` |
| `Total_Alloc_Ordered` | `Total_Alloc_Pending` |
| procurement `Ordered_Qty` | `Pending_Delivery_Qty` **+ new** `Total_Procured_Qty` |
| balance-row `Ordered_Qty` | `Pending_Delivery_Qty` **+ new** `Total_Procured_Qty` |

**Kept deliberately:** `SQM_Achievable_With_Ordered`,
`Coverage_With_Ordered_Pct`, `Completion_With_Ordered_Pct`,
`Fulfillment_With_Ordered_Pct`. Their meaning did not change — they measure
coverage against the *total procured quantity* — only their value corrects. The
UI labels them **"When delivered"** so nobody reads them as "available plus
ordered".

### Two other places carried the additive assumption

* `insights.ts unitBottleneckRateWithOrdered` pooled `available + rawOrdered`
  → now `available + pending`.
* `insights.ts materialBalance` computed `Net_Shortfall = demand − available −
  rawOrdered` → now `demand − Total_Procured_Qty`. **This is the exact formula
  behind the `dashboard_material_balance.xlsx` column that proved the bug.**
* `sme.py smart_calculator` computed `net_shortfall = required − available −
  on_order` → now `required − procured`, and publishes `pending_delivery` and
  `total_procured` as separate fields.

---

## 3. UI terminology

Every second-tier **quantity** now reads **"Pending Delivery"**; every
second-tier **coverage** reads **"When delivered"**. New **"Total Procured"**
columns appear on the Dashboard balance table, the stock-only table, the Smart
Calculator (both tables) and the Total Material Demand export sheet.

The shared `TierNote` legend was rewritten to state the subset relationship
outright:

> **Pending delivery / "when delivered"** is the part of the purchase order that
> has *not* arrived yet (ordered − available). It is … *not* extra stock on top
> of the order: available and pending together are the whole PO.

Tabs touched: Dashboard, Session Report, Location Report, Execution Plan, Total
Overview, Smart Calculator, Executive Summary, Lining Coverage, plus the
location-report workbook and the executive-summary PDF.

---

## 4. Validation

### Suite BC — "Available is part OF Ordered, not extra to it" (16 checks)

* 40 arrived of 100 procured → tier 1 = 40, tier 2 = **60**, not 100
* the two tiers sum to the procured total, never more
* **the GI-8005763 case**: 143,000 of 143,000 → **empty** pipeline; ceiling
  143,000; **9,685 to buy**; and it *appears on the buy list*
* the full contract on a partly-delivered material (30 of 80): tier1 30,
  tier2 50, ceiling 80, buy 20, physical gap 70 — and readiness still counts
  only the 30 that arrived
* over-delivery (120 of 100) → empty pipeline, never negative
* no purchase order at all → empty pipeline
* a **revert-check** stating the outlawed arithmetic explicitly
* **two data checks that read the live workbook**: every row satisfies
  `available ≤ ordered`, and a large share is fully delivered. If the operator's
  data model ever changes, these fail and point at this suite.

### `test:ui-math` section E (7 new checks, 27 total)

The presentation layer's own copy of the maths: `materialBalance` splits the PO
into arrived + pending; the buy list measures against the ceiling (60 of 60
delivered → **40** to buy, where the additive reading clamped to 0 and asked for
nothing); the "when delivered" bottleneck tops out at 60%, not 100%.

### Strict end-to-end comparison against the workbook

A dedicated verification run reconciles the live engine output against the
operator's spreadsheet, material by material:

```
A. tier1 == Available_Qty and tier2 == max(Ordered − Available, 0)
   for all 32 workbook components                                   OK
B. Allocated_Qty never exceeds min(demand, max(available, ordered))  OK
   Demand = Allocated + Shortfall_Qty, Allocated = tier1 + tier2     OK
   Demand = Alloc_Available + Shortfall_Available_Qty                OK
C. every material's Shortage_Qty_To_Buy equals
   max(demand − max(ordered, available), 0)   (30 materials)         OK

   total to buy, additive (old):     106,299.64
   total to buy, subset  (new):     129,250.59
   under-ordered by           :      22,950.95
```

That 22,950.95 matches the independent workbook reconciliation in §1 to the
cent.

**One honest note on precision.** Conservation is asserted to `5e-4`, not to
float exactness. The engine rounds every quantity to 4 dp *independently*
(documented behaviour), so two rounded terms can differ from a third by one ulp
at that precision — 2 of 352 live lines do, e.g. `1699.3356 + 2277.2752 =
3976.6108` against a demand of `3976.6107`. That is pre-existing rounding, not
this change.

### Headline effect

| | before | after |
|---|---|---|
| Buildable **now** | 3,312 m² (7.8%) | **3,312 m² (7.8%)** — tier 1 untouched |
| **When delivered** | 12,430 m² (29.3%) | **10,511 m² (24.8%)** |
| Total to buy | 106,300 units | **129,251 units** |

Readiness is unchanged, as it must be — the subset rule only ever touched the
second tier. Future capacity drops by 1,919 m² and the buy list rises by 22,951
units, both of which were the double-count.

---

## 5. Gates

| Gate | Result |
|---|---|
| Backend service tests | **1018 / 0** (was 1001; +16 BC) |
| SME UI math | **27 / 0** (was 20; +7) |
| SME TS↔PY parity | **1,313 comparisons** ✅ (golden regenerated) |
| Playwright E2E | **53 / 53** |
| Legacy regression | **599 / 0** |
| `tsc -b` + `vite build` + `oxlint` | ✅ |
| `gi_database.db` | sha256 unchanged |

### Test-suite stability, improved along the way

`sme-tiers.spec.ts` loaded Total Overview — the app's most expensive page, a
full model snapshot plus an 85-unit client-side cascade — **four separate
times**, once per test, under four parallel workers. It had already needed its
wait raised to 30 s and blew even that. It is now `mode: 'serial'` with **one
shared page**: four page loads become one, and the four tests run in
**~50–140 ms each** instead of seconds.

`offline-queue.spec.ts` still flakes occasionally under full 4-worker load
(1 in ~3 full runs). It passes **5/5 in isolation**, touches nothing in this
change, and is the same contention class. Reducing the SME spec's load should
help; it is noted, not chased.

---

## 6. What did NOT change

* **Tier 1 / readiness.** `Alloc_Available`, `Status`, `Completion_Pct`,
  `SQM_Achievable_Now`, `Coverage_Now_Pct`, `Fulfillment_Pct` — all identical
  before and after, verified on live data (3,311.88 m² both ways). The
  2026-08-03 tier-segregation rule is untouched.
* **The ERP decoupling** (2026-08-02) — suite BA unchanged and green.
* **Component identity** — suite AZ green; the subset rule applies per
  `(Material_Code, SAP_Code)` like everything else.
* **The workbook and the seed table.** `Initial_Ordered_Qty` still stores the
  operator's `Ordered_Qty` verbatim; the pending quantity is *derived*, never
  stored. Nothing about the import path changed.
