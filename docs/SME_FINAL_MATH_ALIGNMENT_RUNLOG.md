# SME final math alignment — run log

**Date:** 2026-08-04 · **Branch:** `fix/sme-final-math-alignment`

Closes the two findings left open at the end of
[`SME_TIER_SEGREGATION_RUNLOG.md`](SME_TIER_SEGREGATION_RUNLOG.md) §7, both on
explicit operator authorization.

---

## Task 1 — "Overall Coverage" is now the area-weighted bottleneck

### The loophole

The Session Report and Location Report headlined

```
coverage = Σ Available ÷ Σ Demand        (across every material in scope)
```

A **quantity average over unlike materials**. Quantities of Part A and Part B
are not commensurable: hold all of A and none of B and it reads 50% while
nothing at all can be built. The 2026-07-07 STRICT BOTTLENECK ruling forbids
exactly this at unit level — it was leaking back in one aggregation higher up.

### The fix

Each unit already carries its own strict bottleneck rate (`fulfillPct`) and
therefore its own buildable area (`canSqm = sqm × rate`). A scope's coverage is
now

```
coverage = Σ buildable m² ÷ Σ remaining m²
```

which is what the Dashboard has always done (`insights.ts bottleneckScope`). A
unit blocked by one component contributes **0 m²** and cannot be averaged back
up by a neighbour's spare quantity.

Implemented once, in `session.ts`:

```ts
export function scopeBottleneckCoverage(tags: Iterable<TagStat>): ScopeCoverage
```

Both reports call it, so they cannot drift apart again. It returns the physical
figure and the with-ordered twin side by side, keeping rule 1b intact.

**A deliberate difference is documented in the helper**: these tags come from a
CASCADED plan (priority order, pool drains as it goes) while the Dashboard
scores every unit against the full pool. Same formula, different pool
assumption — intended, not drift, and marked "do not reconcile".

### Measured on the live CNCEC model

| Scope | Old (quantity average) | New (area bottleneck) | Overstated by |
|---|---|---|---|
| **All Equipment** (29 tags, 42,403 m²) | **57.7 %** | **7.8 %** | **49.9 pts** |
| Brown Field | 64.5 % | 23.6 % | 40.9 pts |
| TRAIN J | 56.2 % | 3.3 % | 52.9 pts |
| TRAIN K | 54.6 % | **0.4 %** | 54.2 pts |

With-ordered on the all-equipment scope moves 81.9 % → 29.3 %.

**Cross-check:** 7.8 % = 3,311.88 / 42,403.29 m², and 29.3 % = 12,430 m² — both
exactly the engine's own `SQM_Achievable_Now` and `SQM_Achievable_With_Ordered`
totals. The KPI now agrees with the engine instead of contradicting it.

---

## Task 2 — the Smart Calculator uses `(Material_Code, SAP_Code)`

### The loophole

`_CALC_POOL_SQL` keyed stock on `Material_Code` and summed **every variant SAP
under it**, so the four Comp-A/B/C/D drums of a multi-part system each read the
same pooled number. This was the 2026-07-18 pooling rule, and it is the exact
clumping that locked rule 1 removed from the rest of the module: a drum on one
shelf could be reported as covered out of a different drum on a different shelf.

### The fix

* the seed CTE now groups by `(Material_Code, SAP_Code)`, and the join matches
  on **both**;
* the Python side looks the pool up with `sme_engine.mat_key(mat, sap)` — the
  engine's own component identity;
* the line's SAP is normalized with `sme_engine.sap_norm()` before it is used as
  a key, put in the payload, or used to aggregate, so the raw ERP/workbook
  spellings (`"1043 - 2"` vs `"1043-2"`) resolve to one component. The SQL
  normalizes identically with `REPLACE(TRIM(...), ' ', '')` on both sides;
* cross-system aggregation keys on `mat_key` instead of
  `(code, sap, description)` — the description used to ride in the key and could
  split one physical drum into two rows when two systems described it
  differently;
* `pooled_saps` is retired (pinned at 1 for payload compatibility with cached
  bundles) and the "Σ N SAP variants" phrase is gone from both explanation
  strings — there is no pool left to count;
* the UI now **displays the variant SAP** beside the Material Code in both
  tables. Without it the four rows of a multi-part system are indistinguishable,
  which is precisely why the SAP could be dismissed as "an internal id" while
  the pooling bug hid behind it.

### Measured on live data — `GI-8005766`, system code 10, 1,000 m²

Every line used to see the pooled **3,080.5**:

| SAP | required | available (own drum) | shortfall |
|---|---|---|---|
| 1042 | 339.39 | 1,010.00 | 0.00 |
| 1042-1 | 385.19 | 1,010.00 | 0.00 |
| 1042-2 | 808.08 | 1,010.00 | 0.00 |
| **1042-3** | **67.34** | **50.50** | **16.84** |

`1042-3` is a catalyst held at a twentieth of the others' volume. Pooled, it
read 3,080.5 available and reported **no shortage at all**; the system showed
**0 shortfall lines** for a batch that is genuinely 16.84 kg short of being
buildable. It now reports 1.

---

## New gate — `npm run test:ui-math`

`engine.ts` has had a parity gate since Phase S1. The modules between it and the
screen — `session.ts` and `insights.ts` — had **none**, which is how three real
defects survived: the tier merge, the `Material_Code`-vs-`Material_Key` lookup
that pinned the whole Dashboard at 0 %, and the quantity average.

`frontend/scripts/sme_ui_math.mjs` (**20 checks**) closes that. Node ≥23 strips
TypeScript natively, so it needs no bundler and no test framework — only a small
resolver hook (`scripts/register-ts.mjs`) so `import './engine'` finds
`engine.ts`. It asserts on the operator's own example (all of Part A, none of
Part B):

* the quantity average it replaces really does read **50 %** on that fixture;
* `scopeBottleneckCoverage` reads **0 %**, and never exceeds the scarcest
  component;
* area weighting follows AREA, not unit count (300 m² of 400 → 75 %, not 50 %);
* an empty scope is 100 %, never `NaN`;
* `codeStats` keeps both tiers apart, with a **revert-check** that
  `Allocated_Qty ÷ Demand` returns 100 % on the very same unit;
* `insights` coverage is 0 % here and moves to exactly 50 % when Part B is half
  stocked — a broken pool key would stay pinned at 0 %, so this proves the
  lookup genuinely resolves;
* `materialBalance` keeps the two drums of one `Material_Code` apart.

---

## Gates

| Gate | Result |
|---|---|
| Backend service tests | **1001 / 0** (was 999) |
| **SME UI math (new)** | **20 / 0** |
| SME TS↔PY parity | **1,289 comparisons** ✅ |
| Playwright E2E | **53 / 53** — two consecutive clean runs |
| Legacy regression | **599 / 0** |
| `tsc -b` + `vite build` + `oxlint` | ✅ |
| `gi_database.db` | sha256 unchanged |

The engine was not touched: parity is unchanged at 1,289 and the golden is
untouched. Both fixes are presentation-layer and query-layer only.

### One flake found and fixed

`sme-tiers.spec.ts` intermittently timed out opening the Total Overview tab.
That tab fetches the whole snapshot and then runs the full cascade over ~78
units on the browser's main thread, which under four parallel workers
legitimately overruns Playwright's 10 s default. The wait is now 30 s, with the
reason recorded in the spec. Two consecutive full runs are green.

`offline-queue.spec.ts` also flaked once under the same parallel load and passes
in isolation and on both subsequent full runs. It touches nothing in this
change; noted, not chased.
