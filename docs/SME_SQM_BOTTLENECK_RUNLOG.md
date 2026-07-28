# Two-tier allocation (Available vs Ordered) + reverse SQM (run log)

**Date:** 2026-07-29 · **Branch:** `feat/sme-sqm-bottleneck`

| Gate | Before | After |
|---|---|---|
| `backend.api.service_tests` | 895 / 0 | **921 / 0** (+26) |
| SME TS↔PY parity (`sme_parity.mjs`) | pass | **pass** (954 comparisons) |
| Playwright E2E | 39 / 39 | **39 / 39** |
| `legacy/bug_check.py` | 599 / 0 | **599 / 0** |
| `tsc --noEmit` · `npm run build` | ✅ | ✅ |
| `gi_database.db` sha256 | `00652932…ba038` | **unchanged** |

---

## 1. Rulings implemented

| # | Ruling | Where |
|---|---|---|
| Q1 | Two-tier cascade: physical stock first, then on-order | `cascade_allocate` (both engines) |
| Q2a | `effective_ordered = max(Initial_Ordered_Qty − Σreceipts, 0)` | `build_model` |
| Q3 | Surface both achievable figures; deficit measured against physical | `build_sqm_rollup` |
| Q4 | Same priority order for both tiers | pass 2 walks the pass-1 line order |
| Q5 | A system code with no recipe scores **0**, never a silent 100 % | `_achievable` |
| Q6 | `Allocated_Qty` stays the derived sum of both tiers | `cascade_allocate` |
| Q7 | Scoped to the SME lane; main ERP inventory untouched | no `inventory`/`receipts` logic changed |

## 2. Field contract

```
Demand_Qty  = Allocated_Qty + Shortfall_Qty            (conserved)
Allocated_Qty = Alloc_Available + Alloc_Ordered         (Q6)
Shortfall_Available_Qty = the PHYSICAL gap  → drives feasibility, status, coverage
Shortfall_Qty           = the NET gap       → drives the buy list
```

Two shortfalls exist because they answer different questions. *"Can I line this
tank today?"* is physical. *"What do I raise a PR for?"* is net of what is
already on order. Collapsing them into one number is what made the old single
`Allocated_Qty` opaque.

**Feasibility deliberately judges tier 1 only.** A tag whose gap is fully
covered by an inbound PO is *not* "✅ 100% Fully Ready to Build" — you cannot
line a tank with stock that is still on a truck. This also means every
historical status and `Completion_Pct` keeps its exact value.

## 3. The SQM maths

For unit *u* (tag × system code) with remaining area `R`, each recipe material
*m* with rate `rₘ` (`For_1_SQM`) and allocation `allocₘ`:

```
SQM_Achievable(u) = minₘ( allocₘ / rₘ )
SQM_Deficit(u)    = R − SQM_Achievable_Now(u)
```

This is the SQM restatement of the locked 2026-07-07 STRICT BOTTLENECK ruling,
not a new model: `minₘ(allocₘ/rₘ) ≡ minₘ(rateₘ) × R`, and `minₘ(rateₘ)` is
already what `compute_feasibility` computes. No new semantics were introduced.

Edge cases: zero-rate recipe lines impose no ceiling and are excluded from the
minimum; a unit with no positive-rate line scores 0 (Q5). Units are enumerated
from `codes_by_tag`, **not** from allocation lines, so an unmodelled code still
appears in the report instead of silently vanishing.

**System-code rollup is additive** — `SQM_Achievable(code) = Σ_tags
SQM_Achievable(tag, code)`. Averaging the coverage *rates* would be wrong,
because each tag drew from the pool at a different point in the priority order.

## 4. Live impact (CNCEC, full equipment list)

```
tier 2 fired on 159 / 352 allocation lines

  code  n   remaining      now     with ordered      deficit    coverage
     1  28   11,801.0     800.0        9,666.0      11,001.0      6.78%
     2   9    6,259.0     806.4        5,463.3       5,452.6     12.88%
     5  12      747.3     747.3          747.3           0.0    100.00%
     9   5   10,490.0       0.0          384.0      10,490.0      0.00%
     …
 TOTAL      49,435.3    2,587.7       23,814.3      46,847.6

 procurement: gross 386,635 → net to buy 159,449  (227,186 already on order)
```

Two numbers worth dwelling on:

* **2,588 m² buildable today vs 23,814 m² once POs land** — a 9× difference that
  the single `Allocated_Qty` metric could not express at all.
* **227,186 units were about to be re-ordered.** That is 59 % of the old buy
  list, and it is exactly the double-count the Q2a netting closes.

## 5. Golden regeneration — reviewable by construction

`sme_parity_golden.json` was regenerated from the Python oracle and re-verified
by both gates. The diff is deliberately easy to audit:

* **No pre-existing value changed** except on the one line where tier 2 now
  allocates (`TK-B/101/M2`), plus its ordered-pool bookkeeping — 7 values total,
  each listed below.
* **No status changed** in any case.
* New keys only, everywhere else.

```
line[5].Alloc_Ordered              0.0  -> 25.0
line[5].Allocated_Qty              5.0  -> 30.0
line[5].Shortfall_Qty             25.0  ->  0.0
line[5].Fulfillment_With_Ordered  16.67 -> 100.0
line[1]/[5].Ordered_Pool_Before    0.0  -> 25.0
```

`TK-B` stays `🟡 Partially Ready (16.7%)` throughout — the on-order stock closes
the *buy* gap without claiming the tank is buildable.

### Fixture additions (the gate would otherwise pass vacuously)

The first regeneration had **zero** tier-2 allocations: the fixture's on-order
materials were not the ones with gaps, so the new path was never exercised. That
was caught and fixed. The fixture now pins four distinct paths:

| Material | Setup | Exercises |
|---|---|---|
| `M2` | ordered 40, received 15 → 25 usable | tier 2 actually allocating |
| `M1` | ordered 100, received 40 → 60 | the Q2a netting |
| `M6` | ordered 10, received 25 → 0 | over-delivery clamped at zero |
| `TK-D/999` | equipment with no recipe | Q5, on a tag that is otherwise "100% Ready" |

`TK-D/999` is the sharpest of these: `TK-D` reports ✅ *100% Fully Ready to
Build* while carrying 12 m² of unmodelled system. Ruling Q5 is what stops that
12 m² being reported as achievable.

## 6. UI

New **📐 Material-wise segregated report (by system code)** card in the Session
Report tab: per-code remaining / achievable-now / achievable-with-ordered /
deficit / coverage bar, expandable to the blocking materials behind each code
(demand · available · on order · still to buy), with its own **Download PDF** and
**Download Excel** buttons. The combined-procurement table now shows
**Available** and **On Order** as separate columns.

Export key `segregated` renders three sections in a fixed order — *SQM by System
Code*, *Blocking Materials*, *Equipment Detail* — as sheets (xlsx) or
page-broken sections (pdf). CSV falls back to the code rollup alone, since one
flat file cannot carry three schemas.

## 7. Tests — suite AY (+25) and AX (+1)

Aggregation, netting, conservation, the physical/net split, bottleneck-vs-average,
Q5, zero-rate lines, additive rollup, net-of-order procurement, and all three
export formats.

### Revert-verification

| Sabotage | Result |
|---|---|
| `_achievable` averages instead of taking the min | ❌ 2 fail |
| effective-ordered ignores receipts (double-count restored) | ❌ 2 fail |
| feasibility counts on-order stock as buildable | ❌ 1 fail |
| Q5 returns full remaining instead of 0 | ❌ 1 fail |

## 8. Consumers updated

`_overview_rows` and `_material_demand_rows` (both in `sme.py`) and
`weightedProcurement` (`session.ts`) now carry the split, and all three compute
coverage on **physical** stock so every surface agrees with feasibility. The
Total Material Demand sheet gained `Available_Qty` / `Ordered_Qty` beside the
retained `Allocated_Qty`.

## 9. Not done / follow-ups

* **`Initial_Ordered_Qty` is still a workbook snapshot.** Q2a nets receipts off
  it, which is self-correcting but approximate: it assumes every receipt for a
  material discharges that material's outstanding order. Deriving real balances
  from `po_items` (option Q2b) remains the accurate long-term answer.
* **Main ERP inventory untouched** per Q7 — `inventory`/`receipts` have no
  on-order concept outside procurement.
* **Not visually confirmed in a browser.** The preview tool refused
  `http://localhost:5173` throughout this session (it forces `https`). The UI is
  covered by `tsc`, the production build, and endpoint tests that generate the
  real documents — but a human should look at the new card before merge.
