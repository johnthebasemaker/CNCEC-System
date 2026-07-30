# SME component pooling — run log

**Branch** `fix/sme-component-pooling` · **Date** 2026-07-30
**Ruling** COMPONENT IDENTITY — overturns the 2026-07-18 Material_Code pooling rule

---

## 1. The bug

A multi-part chemical system lists **one `Material_Code` as several distinct
physical components**, separated only by the variant SAP:

```
For_1_SQM.xlsx — GI-8005766, system code 10:
  GI-8005766  1042    Cumicrete PU MF 300 - 1mm  0.339394  KG
  GI-8005766  1042-1  Cumicrete PU MF 300 - 1mm  0.385185  KG
  GI-8005766  1042-2  Cumicrete PU MF 300 - 1mm  0.808081  KG
  GI-8005766  1042-3  Cumicrete PU MF 300 - 1mm  0.067340  KG
```

`sme_recipe` already kept these apart (unique on code+material+SAP). The **stock
side did not**: `sme_inventory_seed`'s primary key was `Material_Code` alone, so
the loader summed all four drums into one row and joined their SAPs into a comma
list — `"1041, 1041-1, 1041-2, 1041-3"`. Four unlike things recorded as one
bucket, with the first component's name standing in for all four.

## 2. Why the proposed key would not have worked

The brief proposed `Material_Code + Material_Name + UOM`. Checked against the
actual workbooks, it fails on three counts:

1. **Name is not a discriminator in the recipe file.** All four rows above carry
   the identical name *and* identical UOM. That key still clumps 4→1.
2. **The names disagree across workbooks.** Recipe says `Cumicrete PU MF 300 -
   1mm`; the stock file says `CUMICRETE PU MF 300 (1MM) A`. A name-based key
   could never join the two.
3. **UOM disagrees on 25 of 32 pairs** — recipe `KG` vs stock `Can`/`BAG`/`DR`/
   `EA`. Putting UOM in the key would break the join outright.

`SAP_Code` is non-null in every row of both workbooks and every recipe
`(Material_Code, SAP_Code)` resolves in the stock file. **The key is
`(Material_Code, SAP_Code)`**, whitespace-normalized (the ERP writes `1043 - 2`
for the recipe's `1043-2`).

## 3. What the old grain produced — measured, not asserted

Same data, both engines, one four-component system (100 m² remaining):

| | OLD (pooled) | NEW (per component) | Truth |
|---|---|---|---|
| Comp A shortfall | **0** | 10 | 10 (40 stock, 50 needed) |
| Comp B shortfall | **0** | 10 | 10 |
| Comp C shortfall | 40 | 50 | 50 (20 + 30 on order, 100 needed) |
| Comp D shortfall | **10** | 0 | 0 (30 stock, 10 needed) |
| SQM achievable now | **0** | 20 | 20 (C is the bottleneck) |
| SQM achievable w/ order | **0** | 50 | 50 |
| Report rows | 1, named "…A" | 4 (A/B/C/D) | 4 |

The old model reported **A and B as fully covered when both are 10 short**, and
**D as 10 short when it holds three times what it needs**. The shortfall was not
merely imprecise — it was inverted. All six of these are pinned as `az-revert`
checks.

## 4. Files changed and why

| File | Change |
|---|---|
| `backend/models.py` | `SmeInventorySeed` PK → `(Material_Code, SAP_Code)`; `SAP_Code` NOT NULL default `''` |
| `alembic/…a4e9b1c73f28` | collapses comma lists to their first SAP, guards for duplicates, widens the PK; lossy `downgrade()` documented |
| `backend/api/sme_engine.py` | `sap_norm()`, `mat_key()`, `Material_Key`; pools, `mat_meta`, cascade, totals, procurement, blocking-materials and bottleneck all keyed per component |
| `frontend/src/sme/engine.ts` | identical mirror (`sapNorm`, `matKey`) — changed in the same commit, golden regenerated |
| `backend/api/sme.py` | `SQL_SME_MATERIALS` per-component + ledger reached via the component's **own** SAP; `_material_demand_rows` keyed on `Material_Key`; `_demand_matrix` (a second cascade) fixed; snapshot projections carry SAP |
| `backend/api/bulk_import.py` | `plan_sme_materials` aggregates per `(code, SAP)`; upsert conflict target widened; stale-placeholder retirement |
| `backend/api/sme_master.py` | materials CRUD keyed per component; `sap_code` **required** on PATCH/DELETE |
| `tools/pg_excel_sync.py` | `CONFLICT_KEYS["sme-materials"]` widened; stale retirement |
| `tools/parity_check.py` | supports a spec supplying both sides of the comparison |
| `frontend/src/sme/insights.ts` | `materialMaps` / `demandByMaterial` / balance rows keyed per component |
| `frontend/src/sme/session.ts` | `weightedProcurement` grouped per component |
| `frontend/src/sme/materialCols.tsx` | **new** — shared component rendering |
| 5 SME grids | component columns + `Material_Key` row keys |

### Two latent bugs found on the way

**`materialMaps` (insights.ts) and `_demand_matrix` (sme.py)** both built their
pool with `map.set(code, …)` / `{code: …}` over the material rows. With four
component rows sharing a code that does not pool them — it **discards three of
them**, leaving the last component's stock standing in for all four. Both now key
per component.

## 5. Naming — the operator's actual complaint

> *"Now you given the all same name for the 4 items, It's hard to find the
> materials in the app."*

Two rules, applied everywhere via `materialCols.tsx`:

* **The stock master's name wins.** The recipe repeats one generic name across
  all four rows; the stock master names the real component. Precedence was
  recipe-first, and is now stock-first with the recipe as fallback for a SAP with
  no stock row. Rows now read `CUMICRETE PU MF 300 (1MM) A` … `D`.
* **Names wrap, never ellipse**, and the variant SAP renders under the code.
  `ellipsis: true` truncated `CUMICRETE PU MF 300 (1MM) C` to
  `CUMICRETE PU MF 300 (1MM…` — throwing away the one character that
  distinguishes a component from its three siblings.

`Material_Key` also fixes a real React defect: five grids used
`rowKey="Material_Code"`, which now collides across components.

## 6. Legacy parity, restated as conservation

`tools/parity_check.py` compared `SQL_SME_MATERIALS` row-for-row against the
frozen SQLite `sme_materials_view`. That view pools by `Material_Code` — the very
thing this ruling breaks, so row-for-row parity could only be satisfied by not
fixing the bug.

It is now asserted as the **conservation invariant**: rolled back up to
`Material_Code`, every quantity must match the legacy view exactly. That is the
failure mode worth gating on (a dropped SAP or a double-count), and it stays
green: **5/5**.

⚠️ **This check is partly vacuous and a direct test was added instead.** Every SME
material in the legacy data has `received_qty = consumed_qty = 0`, so both sides
agree trivially on exactly the two columns the ledger-join rewrite touches. Suite
AZ therefore tests it directly — receipts and consumption posted against one
component must move only that component — and reverting the join to the old
`inventory.Material_Code` form fails all four of those checks.

## 7. The cutover path — a deployment defect found and fixed

The frozen legacy SQLite `sme_inventory_seed` **has no `SAP_Code` column at
all**, so a cutover lands every material as one row with `SAP_Code = ''`. The
first workbook sync then inserts the real component rows beside it, leaving a
phantom row carrying the whole pre-split quantity — the same physical stock
counted twice.

Retirement is scoped on **two** conditions, the second learned the hard way:

1. the workbook supplied a real SAP for that `Material_Code`; **and**
2. no SAP-**less** *recipe* line still references it.

The first version had only condition 1, and the browser check caught it
immediately: coverage collapsed to **0.0% across all 29 equipment**. The frozen
legacy DB carries 86 pre-workbook recipe rows with no SAP, and those lines can
only draw on a blank-SAP seed row — retiring it left them with a zero pool.

With both guards, an additive sync onto a fresh cutover retires only the **2**
genuinely orphaned placeholders and **holds 20**, with a message naming the
remedy. The documented **SME reseed** (`pg_excel_sync --sme-reseed`) replaces
both sides from the workbook and converges to the real working state:
`sme_recipe` 41/0 blank, `sme_inventory_seed` 32/0 blank — exactly what the live
dev database holds.

**I checked before deleting.** The 86 legacy blank-SAP recipe rows and the 30
workbook-coded pairs are **disjoint** (measured: 0 overlap), so they are real
recipe data the workbook does not cover, not stale duplicates. Nothing deletes
them.

## 8. Verification

| Gate | Before | After |
|---|---|---|
| `service_tests` | 946 / 0 | **951 / 0** |
| SME TS↔PY parity | pass (954) | **pass (1276)** |
| Derived-view parity | 5 / 5 | **5 / 5** (fresh cutover) |
| Playwright E2E | 42 / 42 | **42 / 42** |
| `legacy/bug_check.py` | 599 / 0 | **599 / 0** |
| `tsc -b` · `npm run build` · `oxlint` | ✅ | ✅ |
| `gi_database.db` sha256 | `00652932…ba038` | **unchanged** |

New suite **AZ** (20 checks): pools, dirty-SAP normalization, component naming,
per-component shortfall, conservation, the bottleneck as a specific drum, four
`az-revert` checks against the old grain, four `az-sql` checks on the derived
availability SQL, and five `az-cutover` checks on convergence.

### The golden diff is reviewable by construction

* **8 added rows** — all the new `TK-E` / code-8 / `M7` component rows.
* **2 changed pre-existing values**: procurement `M5`'s `Material_Name` `""` →
  `"Rubber Sheet"` and `UOM` `""` → `"SQM"`. A buy-list row for a material with
  no stock master row used to render blank; it now falls back to the recipe name.
* **0 numeric changes. 0 status changes.**

The fixture's new system code 8 was designed so the new path cannot pass
vacuously: all four recipe rows share one `Material_Code` **and one generic
name** (exactly as the real workbook writes them), component C's stock row writes
its SAP dirty as `"77 - 2"`, and the old grain gives a materially different
answer (0 m² vs 20 m²).

### Also fixed

`_demand_matrix` rounded demand, allocated and shortfall independently, so
`allocated + shortfall == demand` could drift 1e-4 per line. At CNCEC's ~1,400
lines that no longer hid inside the reconciliation tolerance. It now rounds once
and derives, and the totals accumulate the same rounded figures the lines
report. The related test's flat `1e-3` tolerance was a bet on the row count, not
a bound; it is now `n_totals × 5e-4`, the actual 3-dp rounding bound.

## 9. Caveats

1. **Ruling Q2 is in force**: stock quantities are taken to be in the recipe's
   UOM. The stock UOM label disagrees on 25 of 32 pairs (`KG` vs `Can`/`BAG`) and
   is treated as display-only. `PACKAGE SIZE` is blank for every PU component, so
   no conversion factor exists in the data. A real pack-size table remains
   outstanding.
2. **The migration cannot recover per-component quantities** from a pooled row —
   nothing can, the information was destroyed on load. It makes the schema
   correct and leaves the surviving row valid but stale; the workbook reload
   converges it exactly. This is stated in the migration docstring.
3. **The ERP `inventory` table has a UNIQUE on `Material_Code`**, so the variant
   SAPs cannot all carry the same Material_Code there — the sync reports this per
   SAP (`1042-2: Material_Code GI-8005766 already used by SAP 1042`). Pre-existing
   and untouched; the SME lane does not depend on it, but it means the ERP side
   still cannot express component identity. Worth a separate decision.
4. **Scope held to the SME estimator and procurement views** per ruling Q7. Main
   ERP inventory logic is untouched.
5. **A dev-database schema change was applied** (`alembic upgrade head`) plus a
   `sme-materials` workbook sync, because the suites cannot run against the old
   primary key. The dev mirror is rebuildable and the migration has a
   `downgrade()`.
