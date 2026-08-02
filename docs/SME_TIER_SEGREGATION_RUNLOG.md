# SME strict tier segregation — run log

**Date:** 2026-08-03 · **Branch:** `fix/sme-strict-tier-segregation`

> **The report.** "Phenadin ACP Powder has 0 physical stock available, but we
> have it On Order. The SME portal is currently showing that we can execute
> builds with it. This violates our locked rule: *Feasibility judges physical
> tier only. A tank cannot be built with a purchase order.*"

The report was correct, and the diagnosis "ordered stock is acting like physical
stock in the UI **or** the engine" resolves cleanly to **the UI**. The engine was
right the whole time.

---

## 1. Reproduced first, on live data

`PHENACIN ACP POWDER` — `GI-8005761` / SAP `1038`:

```
sme_inventory_seed :  Initial_Available_Qty = 0      Initial_Ordered_Qty = 56,350
sme_recipe         :  system code 6 @ 8.865 /m²   ·   system code 7 @ 9.46 /m²
```

Running the Python engine against the live CNCEC snapshot:

```
sqm_by_code 6 : SQM_Achievable_Now 0.0   Coverage_Now_Pct 0.0   With_Ordered 2,384
sqm_by_code 7 : SQM_Achievable_Now 0.0   Coverage_Now_Pct 0.0   With_Ordered 3,722.6
feasibility   : 🔴 Blocked by Shortages, Completion_Pct 0.0   (all 9 tags)
```

**The engine already obeyed the rule.** `cascade_allocate` has a clean two-tier
split, `compute_feasibility` bottlenecks on `Alloc_Available`, and
`build_sqm_rollup` computes `SQM_Achievable_Now` from `Alloc_Available`. Nothing
in `sme_engine.py` or `engine.ts` needed a semantic fix.

## 2. Where it actually broke: every layer ABOVE the engine

The engine publishes three quantities per allocation line:

| Field | Meaning |
|---|---|
| `Alloc_Available` | TIER 1 — drums on the shelf |
| `Alloc_Ordered` | TIER 2 — drums on an open purchase order |
| `Allocated_Qty` | **tier 1 + tier 2**, a *conservation* field so that `Demand = Allocated + Shortfall_Qty` |

`Allocated_Qty` is not a readiness quantity. Six separate presentation layers
divided it by `Demand_Qty` and presented the result as coverage.

### 2.1 `frontend/src/sme/session.ts` — `codeStats()`, the big one

```ts
const rate = ln.Demand_Qty > 0 ? Math.min(1, ln.Allocated_Qty / ln.Demand_Qty) : 1   // ← the bug
```

One function, and it feeds **`StatusDot`, `FulfilPill`, `canSqm`, `shortSqm` and
every "Coverage" figure** in the Session Builder, Session Report, Location
Report, Execution Plan and every per-equipment expander. With ACP at 0 physical
and a PO covering the whole demand, `Allocated_Qty == Demand_Qty` → rate 1.0 →
a green **100%** pill labelled ready to build.

### 2.2 `TotalOverview.tsx` — the master grid

* `pct` = `Σ Allocated_Qty ÷ Σ Demand` → the **"Fulfil %"** column, the row tint,
  and the **"Fully Ready (100%)" status filter**. A unit with nothing on the
  shelf was *listed under the ready filter*.
* "Shortfall" showed the NET gap (post-PO) under a name operators read as physical.
* The per-code expander grouped materials by `Material_Code` and looked stock up
  by `Material_Code` — a **component-identity violation** that let one of the
  four drums of a multi-part system stand in for all four.

### 2.3 `insights.ts` — the Dashboard, a second and opposite defect

```ts
const rate = Math.min(1, (avail.get(r.Material_Code) ?? 0) / rowDemand)   // ← wrong key
```

`avail` is keyed by `matKey()` = `` `${Material_Code}|${SAP_Code}` ``. Looking it
up by `Material_Code` alone **never matches**, so every lookup fell through to
`0` and `unitBottleneckRate()` returned 0 for any unit with a positive-demand
recipe line. The whole Dashboard read **0% coverage**. Introduced when the
2026-07-30 component ruling re-keyed the maps without re-keying this call site;
it errs safe, which is why nobody reported it.

### 2.4 `exec_summary.py` — `_capacity_from_lines()`

```python
rate = min(max(r["Allocated_Qty"] / r["Demand_Qty"], 0.0), 1.0)
```

Feeds the **HOD Executive Summary** — whose card is literally titled *"Achievable
SQM with available material"* — its Excel export, its server-rendered PDF, and
`/analytics/lining-coverage`.

### 2.5 `ExecutionPlan.tsx` — two false statements, in words

The plan listed only lines with a NET shortfall, so a material covered entirely
by a PO vanished from it, and the page then printed:

> "This system code is fully covered by current stock."
> "✅ {tag} is fully buildable with current stock — no procurement needed."

Both were false exactly when the gap sat on a purchase order.

### 2.6 `sme.py` `_location` export + `sme_export_layouts`

The Location Report workbook's main table carried one `Allocated_Qty` column
beside a physical-only `Fulfillment_Pct` — two different meanings, one row.

### What was already correct (verified, unchanged)

* `sme_engine.py` / `engine.ts` cascade, feasibility and reverse-SQM.
* `sme.py` `_overview_rows()` and `_material_demand_rows()` — the **server-rendered**
  exports were right all along and used `Alloc_Available` for `Fulfillment_Pct`.
  So the exported Total Overview and the on-screen Total Overview **disagreed**;
  that is corroborating evidence, not a coincidence.
* `session.ts weightedProcurement()` — coverage already on `Available_Qty`.
* `insights.ts materialBalance()` — `Coverage_Pct` physical, `Shortfall` vs
  `Net_Shortfall` already separated.
* The SessionReport Material-Wise Segregated card — already showed
  Achievable-now / With-ordered side by side.
* `MatrixReports.tsx` (Equipment Report, System Code Report) — no demand math at
  all; nothing to fix.
* Smart Calculator — physical-only already, but had **no on-order column**.

## 3. Measured impact, on the live snapshot

Running the old `codeStats` formula and the new one over the same 85 (tag, code)
units of the live CNCEC model:

| | |
|---|---|
| units whose coverage was inflated by tier 2 | **24 of 85** |
| units shown as **100% "Fully Ready"** that were not buildable | **18** |
| "buildable" area claimed | **12,430 m²** |
| actually buildable today | **3,312 m²** |
| **overstatement** | **9,118 m² — 21.5 % of the remaining programme** |

The 12,430 figure is exactly `SQM_Achievable_With_Ordered` — final proof the UI
was rendering the forecast in the readiness slot.

---

## 4. The fix

### 4.1 Engine — additive only, both languages, one commit

No numeric behaviour changed. Three **new** fields, so that no consumer ever has
to re-derive a forward-looking number from `Allocated_Qty`:

| New field | Where |
|---|---|
| `Completion_With_Ordered_Pct` | `compute_feasibility` |
| `Coverage_With_Ordered_Pct` | `build_sqm_rollup`, `build_sqm_by_code` |

(`Fulfillment_With_Ordered_Pct` on the line already existed.) Golden regenerated;
TS↔PY parity **1,289 comparisons**.

The rule is now written into both module docstrings:

> TIER 1 (`Alloc_Available`) is the ONLY input to readiness. TIER 2
> (`Alloc_Ordered`) feeds ONLY the forward-looking twins and the NET buy list.
> `Allocated_Qty` is a conservation field; dividing it by `Demand_Qty` does not
> produce a coverage percentage anything may colour green.

### 4.2 Every tab, audited and fixed

| Tab | What changed |
|---|---|
| **Dashboard** | fixed the `Material_Key` lookup bug (coverage was stuck at 0%); KPIs split into **Buildable now SQM** / **With ordered SQM** and **Coverage now** / **With ordered**; balance table columns → *Short (physical)* / *To buy (net)* / *Ready now %*; system-code table gained *With ordered SQM* + *With ordered %*; "Critical (<50%)" is explicitly physical |
| **Session Builder** | inherits the fixed `codeStats` — dots and pills are now physical |
| **Session Report** | overall coverage KPI split into **Coverage now** + **With ordered**; summary strip shows Available / On order / To procure; segregated table gained a *With ordered* coverage column; per-equipment expanders show the forecast as a separate amber hint |
| **Location Report** | the KPI titled **"Available SQM"** was showing the with-ordered number — now **Buildable now SQM** (physical) plus a separate **With ordered SQM**; overall coverage physical; per-tag rows carry an amber "→ x% ordered" hint |
| **Execution Plan** | material columns split into Available / On Order / Short (physical) / To Order; both false "fully covered by current stock" claims rewritten; **new**: materials that block the build but have nothing left to buy are now surfaced instead of vanishing ("Nothing left to buy — but not buildable today") |
| **Total Overview** | `pct` → tier-1 bottleneck from `codeStats`; columns split Available / On Order / Short (physical) / To buy (net) / **Ready now %** / **With ordered %**; status filter relabelled and now runs on physical; KPI strip split; per-code expander re-keyed to `Material_Key` (component-identity fix) and shows Stock available / Stock on order |
| **Equipment Report** | audited — pure structure/SQM, no demand math, no change needed |
| **System Code Report** | same |
| **Smart Calculator** | backend now returns `ordered_stock` and `net_shortfall_qty`; new **On Order** column; explanation string says "short X, Y to buy after Z on order" |
| **Executive Summary** | `_capacity_from_lines` → `Alloc_Available`; card retitled "with stock ON HAND"; new *With ordered* / *With ordered %* columns in the page, the Excel export and the PDF; the bottleneck material is now chosen on the **physical** gap, so a fully-ordered material still names itself as the blocker |
| **Lining Coverage** | same rollup — *Achievable now* vs *With ordered* |
| **Location Report export** | workbook columns split into both tiers plus both gaps; `SAP_Code` added |

A shared **`TierNote`** legend (green = available now · amber = on order · red =
to buy) now heads the Dashboard, Session Report, Location Report, Execution Plan
and Total Overview, because the numbers alone could not carry the distinction.

---

## 5. Data integrity — the decoupling still holds

The 2026-08-02 strict decoupling is untouched and re-verified:

* The tier fix reads only `pool_init` / `pool_ordered_init`, both built from
  `sme_inventory_seed`. Suite **BA** (11 checks) still passes unchanged — an ERP
  receipt, issue or return moves no SME number, and every SME read is
  byte-identical across real warehouse movement.
* `bb-decoupled` re-asserts it from inside the new suite.
* `gi_database.db` sha256 `00652932…ba038` **unchanged**.

**Multi-component materials** re-verified under the fixed logic (`bb-component`):
one `Material_Code`, two drums — A fully stocked, B empty but fully on order →
the unit is **0% ready** (B is the bottleneck) and 100% with ordered. A's stock
cannot stand in for B. Suite AZ's 20 component-identity checks all still pass.

---

## 6. Tests

**Suite BB — "a purchase order never reads as readiness"** (18 checks), built
around the exact ACP shape: 0 physical, 500 on order, demand 100.

* the line splits 0 / 100 / 100 across the three fields
* `Fulfillment_Pct` 0 while `Fulfillment_With_Ordered_Pct` 100
* physical gap 100 **and** net gap 0 — the two shortfalls never collapse
* `SQM_Achievable_Now` **0 of 100 m²** ← the reported bug
* status BLOCKED, `Completion_Pct` 0, forecast parked in its own field
* ACP named as bottleneck **despite zero net shortfall**
* nothing on the buy list (re-ordering is the expensive half of this bug)
* the export builders and the executive-summary rollup agree
* **control**: move the same 500 units to `available` → fully ready. The rule is
  about *where* the stock is, not a blanket discount.
* **revert-check**: the outlawed formula returns 100% on the same line

**`tests/e2e/specs/sme-tiers.spec.ts`** (4 tests) with a seeded fixture
(`E2E-TIER-TANK`, code 9101, 0 available / 5,000 on order) proves it in a real
browser: the grid has both columns and no merged "Allocated"; the KPI strip has
both figures; the code header renders **`0.0%` + `100.0% with ordered`**; the
expander separates Stock available from Stock on order and the fully-stocked
sibling still reads 100%.

| Gate | Result |
|---|---|
| Backend service tests | **999 / 0** (was 978; +18 BB, +2 exec, +1) |
| SME TS↔PY parity | **1,289 comparisons** ✅ |
| Playwright E2E | **53 / 53** (was 49; +4) |
| Legacy regression | **599 / 0** |
| `tsc -b` + `vite build` + `oxlint` | ✅ |
| `gi_database.db` | sha256 unchanged |

---

## 7. Other SME rules — audited, with findings

| Locked rule | Status |
|---|---|
| Component identity `(Material_Code, SAP_Code)` | ✅ — and **one violation found and fixed**: Total Overview's per-code expander grouped by `Material_Code` and looked stock up by `Material_Code`, so one drum's stock stood in for all four |
| STRICT BOTTLENECK (least-available material caps the unit) | ✅ at unit level everywhere — see the finding below for scope-level KPIs |
| Unmodelled system code scores 0, never a silent 100% | ✅ `_achievable()` returns 0 with no positive-rate line; `Has_Recipe` flagged |
| Half-up rounding shared verbatim | ✅ untouched |
| Both engines change together | ✅ same commit, golden regenerated, parity green |
| Strict decoupling from the ERP ledger | ✅ suite BA unchanged and green |
| `sme_inventory_seed` never mingles with ERP `inventory` | ✅ |
| `gi_database.db` untouchable | ✅ sha unchanged |

### Finding 1 — scope-level "Overall Coverage" is a quantity average, not a bottleneck

The Session Report and Location Report compute their session-wide coverage KPI as
`Σ Available ÷ Σ Demand` across every material in scope. That is a *quantity*
ratio; the Dashboard uses the principled area-weighted bottleneck
(`Σ(remaining × unit bottleneck) ÷ Σ remaining`). The two can disagree, and the
quantity average can read comfortably high while the scarcest component makes
nothing buildable.

I did **not** change it: it is pre-existing, it is not a tier merge (both sides
are now honestly tier 1), and redefining an operator-facing KPI is a bigger
decision than the reported bug. The help text now states the formula explicitly.
Say the word and I will move both to the Dashboard's bottleneck definition.

### Finding 2 — the Smart Calculator still pools stock per `Material_Code`

It sums availability across every variant SAP sharing a `Material_Code`, which is
in tension with the component-identity rule (four unlike drums, one bucket). It
is a separate 2026-07-18 ruling and was out of scope here; only the *source*
changed on 2026-08-02 (ERP ledger → SME seed). Carried forward from the previous
run log.

### Finding 3 — `/analytics/lining-coverage` still reads the live ERP ledger

Unchanged from the 2026-08-02 decoupling run log §6.1 — it is a deliberate
warehouse-stock view, not the estimator. Its *tier* handling is now fixed (it
shares `_capacity_from_lines`), so it no longer counts on-order stock as
achievable. Whether the page should exist at all remains an operator ruling.
