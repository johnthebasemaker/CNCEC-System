#!/usr/bin/env node
/**
 * frontend/scripts/sme_ui_math.mjs — gate for the SME PRESENTATION math.
 *
 * `engine.ts` has had a parity gate since Phase S1. The modules that sit
 * between it and the screen — `session.ts` (codeStats / tagStats /
 * scopeBottleneckCoverage) and `insights.ts` (unitBottleneckRate, coverage
 * rollups) — had none, and that is precisely where two real defects lived
 * undetected:
 *
 *   · 2026-08-03 — codeStats derived its readiness rate from `Allocated_Qty`
 *     (physical PLUS on order), so a material with nothing on the shelf and a
 *     full purchase order rendered a green "100% Fully Ready" pill. 18 of 85
 *     live units were affected; buildable area was overstated by 21.5%.
 *   · 2026-08-03 — insights.unitBottleneckRate looked the pool up by
 *     `Material_Code` against a map keyed by `Material_Key`, so every lookup
 *     missed and the whole Dashboard read 0% coverage.
 *   · 2026-08-04 — the Session/Location "Overall Coverage" KPI was a QUANTITY
 *     average across unlike materials: all of Part A and none of Part B read
 *     as 50% when nothing could be built.
 *
 * Run:  npm run test:ui-math   (from frontend/)
 */
import { buildModel, matKey, runPlan } from '../src/sme/engine.ts'
import { codeStats, scopeBottleneckCoverage, tagStats, weightedProcurement } from '../src/sme/session.ts'
import { materialBalance, pairCoverage, scopeCoverage, allUnits } from '../src/sme/insights.ts'

let passed = 0
const failures = []

function check(name, ok, detail = '') {
  if (ok) {
    passed += 1
    console.log(`  ✅ ${name}`)
  } else {
    failures.push(name)
    console.log(`  ❌ ${name}${detail ? `  — ${detail}` : ''}`)
  }
}

const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol

// ── The fixture: ONE tank, ONE system code, 100 m² remaining, two materials
// at 1.0 per m² — so demand is 100 each.
//   PART-A : 100 arrived of 100 procured → fully delivered, empty pipeline
//   PART-B :   0 arrived of 500 procured → the blocker (the ACP Powder shape)
// 2026-08-05 SUBSET RULE: `ordered_qty` is the TOTAL procured and
// `available_qty` is the arrived part OF it, so Part A's pipeline is 0 (not
// another 100) and Part B's is the full 500.
// Quantity average  = (100 + 0) / (100 + 100) = 50%
// Area bottleneck   = 0%   ← nothing can be built without Part B
const RATE = 1.0
function model({ aAvail = 100, aOrd = 100, bAvail = 0, bOrd = 500 } = {}) {
  return buildModel(
    [{ Equipment_Tag_No: 'TK-1', Name: 'Tank 1', Location: 'L1',
      Lining_System_Code: '1', Surface_Area_SQM: 100 }],
    [{ Lining_System_Code: '1', Lining_System_Name: 'SYS', Material_Code: 'M',
      SAP_Code: 'A', Material_Name: 'Part A', UOM: 'KG', For_1_SQM: RATE },
    { Lining_System_Code: '1', Lining_System_Name: 'SYS', Material_Code: 'M',
      SAP_Code: 'B', Material_Name: 'Part B', UOM: 'KG', For_1_SQM: RATE }],
    [{ material_code: 'M', sap_code: 'A', material_name: 'Part A', uom: 'KG',
      available_qty: aAvail, ordered_qty: aOrd },
    { material_code: 'M', sap_code: 'B', material_name: 'Part B', uom: 'KG',
      available_qty: bAvail, ordered_qty: bOrd }],
    [])
}

console.log('\n A. session.ts codeStats / tagStats — TIER SEGREGATION')
{
  const m = model()
  const plan = runPlan(m, ['TK-1'])
  const cs = [...codeStats(plan.lines).values()][0]

  check('codeStats readiness is TIER 1 — Part B has nothing on the shelf, so '
    + 'the unit is 0% ready even though a PO covers the whole gap',
  cs.fulfillPct === 0, `got ${cs.fulfillPct}%`)
  check('codeStats publishes the forecast separately at 100%',
    cs.fulfillWithOrderedPct === 100, `got ${cs.fulfillWithOrderedPct}%`)
  check('canSqm is physical (0 m²) and canSqmWithOrdered is the forecast (100 m²)',
    cs.canSqm === 0 && cs.canSqmWithOrdered === 100,
    `${cs.canSqm} / ${cs.canSqmWithOrdered}`)
  check('the two shortfalls stay apart — 100 physically short, 0 left to buy',
    cs.shortfallAvailable === 100 && cs.shortfall === 0,
    `phys=${cs.shortfallAvailable} net=${cs.shortfall}`)
  check('the tiers are reported as their own quantities — 100 arrived, and '
    + "100 drawn from Part B's pipeline",
  cs.allocAvailable === 100 && cs.allocPending === 100,
  `av=${cs.allocAvailable} pending=${cs.allocPending}`)

  // The formula this gate exists to outlaw.
  const outlawed = Math.min(1, cs.alloc / cs.demand) * 100
  check('REVERT-CHECK: Allocated_Qty ÷ Demand returns 100% on this very unit — '
    + 'the formula that produced the false "Ready to Build" pills',
  outlawed === 100 && cs.fulfillPct === 0, `outlawed=${outlawed}`)

  const ts = [...tagStats(m, plan.lines).values()][0]
  check('tagStats rolls up the WORST unit for both tiers',
    ts.fulfillPct === 0 && ts.fulfillWithOrderedPct === 100,
    `${ts.fulfillPct} / ${ts.fulfillWithOrderedPct}`)
}

console.log('\n B. session.ts scopeBottleneckCoverage — AREA-WEIGHTED BOTTLENECK')
{
  const m = model()
  const plan = runPlan(m, ['TK-1'])
  const stats = tagStats(m, plan.lines)
  const scope = scopeBottleneckCoverage(stats.values())

  // This is the operator's own example, verbatim: "if we have 100% of Part A
  // and 0% of Part B, it implies we have 50% coverage, even though the true
  // bottleneck means we can build nothing."
  const totals = [...codeStats(plan.lines).values()][0]
  const quantityAverage = (totals.allocAvailable / totals.demand) * 100
  check('the QUANTITY average this replaces reads 50% on the fixture',
    near(quantityAverage, 50), `got ${quantityAverage}%`)
  check('scope coverage is 0% — a unit blocked by one component contributes '
    + '0 buildable m², and no sibling quantity can average it back up',
  scope.coveragePct === 0, `got ${scope.coveragePct}%`)
  check('the scope reports its area explicitly (0 of 100 m² buildable)',
    scope.canSqm === 0 && scope.sqm === 100, `${scope.canSqm} / ${scope.sqm}`)
  check('the with-ordered twin is carried separately at 100%',
    scope.coverageWithOrderedPct === 100 && scope.canSqmWithOrdered === 100,
    `${scope.coverageWithOrderedPct}% ${scope.canSqmWithOrdered}m²`)
  check('scope coverage NEVER exceeds the scarcest component — the whole point',
    scope.coveragePct <= totals.fulfillPct,
    `scope=${scope.coveragePct} worst=${totals.fulfillPct}`)
}

console.log('\n C. scopeBottleneckCoverage — area weighting across units')
{
  // Two tanks of different size, one fully buildable, one fully blocked.
  // Area weighting must follow the AREA, not the unit count.
  const stats = [
    { sqm: 300, canSqm: 300, canSqmWithOrdered: 300 },
    { sqm: 100, canSqm: 0, canSqmWithOrdered: 100 },
  ]
  const s = scopeBottleneckCoverage(stats)
  check('300 m² buildable of 400 → 75%, not the 50% a per-unit average gives',
    s.coveragePct === 75, `got ${s.coveragePct}%`)
  check('an empty scope is 100%, never NaN',
    scopeBottleneckCoverage([]).coveragePct === 100)
}

console.log('\n D. insights.ts — the pool must be keyed by Material_Key')
{
  const m = model()
  const units = allUnits(m)
  const materials = [
    { material_code: 'M', sap_code: 'A', material_name: 'Part A', uom: 'KG',
      available_qty: 100, ordered_qty: 0 },
    { material_code: 'M', sap_code: 'B', material_name: 'Part B', uom: 'KG',
      available_qty: 0, ordered_qty: 500 },
  ]
  const cov = scopeCoverage(m, units, materials)
  check('Dashboard scope coverage is 0% here — NOT 100%, which is what a '
    + 'Material_Code lookup against a Material_Key map used to return via `?? 0`',
  cov.coveragePct === 0, `got ${cov.coveragePct}%`)

  // Prove the lookup really resolves: give Part B half its demand and expect 50%.
  const half = scopeCoverage(m, units, [
    materials[0], { ...materials[1], available_qty: 50 }])
  check('halving Part B moves coverage to exactly 50% — the pool lookup '
    + 'genuinely resolves (a broken key would stay pinned at 0%)',
  half.coveragePct === 50, `got ${half.coveragePct}%`)

  check('the Dashboard also publishes the with-ordered twin',
    cov.coverageWithOrderedPct === 100, `got ${cov.coverageWithOrderedPct}%`)

  const pair = pairCoverage(m, units, materials)[0]
  check('pairCoverage agrees per (tag, code): 0% now, 100% with ordered, '
    + 'deficit measured against PHYSICAL stock',
  pair.coveragePct === 0 && pair.coverageWithOrderedPct === 100
    && pair.deficitSqm === 100, JSON.stringify(pair))

  const bal = materialBalance(m, units, materials)
  const byKey = Object.fromEntries(bal.rows.map((r) => [r.Material_Key, r]))
  check('materialBalance keeps the two drums of ONE Material_Code apart',
    Object.keys(byKey).sort().join() === [matKey('M', 'A'), matKey('M', 'B')].sort().join(),
    Object.keys(byKey).join())
  check('Part B shows a 100 physical shortfall and 0 net (the PO covers it)',
    byKey[matKey('M', 'B')].Shortfall === 100
    && byKey[matKey('M', 'B')].Net_Shortfall === 0,
    JSON.stringify(byKey[matKey('M', 'B')]))
}

console.log('\n E. THE SUBSET RULE — available is part OF ordered, not extra to it')
{
  // 2026-08-05. `insights.ts` carried the additive assumption in two places:
  // the "with ordered" bottleneck pooled `avail + rawOrdered`, and
  // materialBalance computed `Net_Shortfall = demand − avail − rawOrdered`.
  // Both double-counted every unit already on the shelf.
  const units = allUnits(model())
  const m = model()

  // ONE material, 100 m² of demand, FULLY DELIVERED: 60 arrived of 60 ordered.
  const oneMat = buildModel(
    [{ Equipment_Tag_No: 'TK-S', Lining_System_Code: '1', Surface_Area_SQM: 100 }],
    [{ Lining_System_Code: '1', Lining_System_Name: 'SYS', Material_Code: 'M',
      SAP_Code: 'S', Material_Name: 'Mat', UOM: 'KG', For_1_SQM: 1.0 }],
    [{ material_code: 'M', sap_code: 'S', material_name: 'Mat', uom: 'KG',
      available_qty: 60, ordered_qty: 60 }], [])
  const mats = [{ material_code: 'M', sap_code: 'S', material_name: 'Mat',
    uom: 'KG', available_qty: 60, ordered_qty: 60 }]
  const bal = materialBalance(oneMat, allUnits(oneMat), mats)
  const row = bal.rows[0]

  check('materialBalance splits the PO into arrived and pending — 60 of 60 '
    + 'delivered leaves an EMPTY pipeline, not another 60',
  row.Pending_Delivery_Qty === 0 && row.Total_Procured_Qty === 60,
  `pending=${row.Pending_Delivery_Qty} procured=${row.Total_Procured_Qty}`)
  check('the buy list measures against the PROCURED ceiling: 100 − 60 = 40. '
    + 'The additive reading (100 − 60 − 60) clamped to 0 and asked for nothing',
  row.Net_Shortfall === 40 && Math.max(100 - 60 - 60, 0) === 0,
  `net=${row.Net_Shortfall}`)
  check('the PHYSICAL gap is unaffected by any of this — 100 − 60 = 40',
    row.Shortfall === 40, `short=${row.Shortfall}`)

  const cov = scopeCoverage(oneMat, allUnits(oneMat), mats)
  check('the "when delivered" bottleneck tops out at the PROCURED ceiling '
    + '(60%), not at available + ordered (which would read 100%)',
  near(cov.coverageWithOrderedPct, 60) && near(cov.coveragePct, 60),
  `now=${cov.coveragePct} delivered=${cov.coverageWithOrderedPct}`)

  // Partly delivered: 20 arrived of 80 procured against 100 demand.
  const partMats = [{ material_code: 'M', sap_code: 'S', material_name: 'Mat',
    uom: 'KG', available_qty: 20, ordered_qty: 80 }]
  const partModel = buildModel(
    [{ Equipment_Tag_No: 'TK-S', Lining_System_Code: '1', Surface_Area_SQM: 100 }],
    [{ Lining_System_Code: '1', Lining_System_Name: 'SYS', Material_Code: 'M',
      SAP_Code: 'S', Material_Name: 'Mat', UOM: 'KG', For_1_SQM: 1.0 }],
    partMats, [])
  const pc = scopeCoverage(partModel, allUnits(partModel), partMats)
  const pr = materialBalance(partModel, allUnits(partModel), partMats).rows[0]
  check('partly delivered: 20 arrived of 80 procured → 20% ready now, 80% when '
    + 'delivered (not 100% from 20 + 80)',
  near(pc.coveragePct, 20) && near(pc.coverageWithOrderedPct, 80),
  `now=${pc.coveragePct} delivered=${pc.coverageWithOrderedPct}`)
  check('…and 60 is still in the pipeline, with 20 left to buy',
    pr.Pending_Delivery_Qty === 60 && pr.Net_Shortfall === 20,
    `pending=${pr.Pending_Delivery_Qty} buy=${pr.Net_Shortfall}`)

  // Over-delivery must not produce a negative pipeline.
  const overMats = [{ material_code: 'M', sap_code: 'S', material_name: 'Mat',
    uom: 'KG', available_qty: 120, ordered_qty: 100 }]
  const over = materialBalance(partModel, allUnits(partModel), overMats).rows[0]
  check('more arrived than ordered leaves an EMPTY pipeline and a ceiling of '
    + 'the larger figure — never a negative pending quantity',
  over.Pending_Delivery_Qty === 0 && over.Total_Procured_Qty === 120,
  `pending=${over.Pending_Delivery_Qty} procured=${over.Total_Procured_Qty}`)

  void units; void m
}

// ─── F. the Session Report's exported quantities ──────────────────────────────
// `Total Procured` was added to the Session Report drill-down exports on
// 2026-08-04. It is the one column an export layer is most likely to get wrong,
// because the intuitive way to produce it — available + pending — is exactly
// the additive reading the subset rule overturned. Pinned here so a future
// refactor of the export block fails the gate instead of shipping a buy list
// that is short by everything already on the shelf.
console.log('\n F. Session Report exports — Total Procured is the CEILING')
{
  // 100 m² of demand at 1 KG/m². 40 KG have ARRIVED out of 100 PROCURED, so
  // the pending delivery is 60 — never another 100.
  const m = buildModel(
    [{ Equipment_Tag_No: 'T1', Lining_System_Code: 'C1', Surface_Area_SQM: 100 }],
    [{ Lining_System_Code: 'C1', Lining_System_Name: 'SYS', Material_Code: 'M1',
      SAP_Code: 'S1', Material_Name: 'Partly delivered', UOM: 'KG',
      For_1_SQM: RATE }],
    [{ material_code: 'M1', sap_code: 'S1', material_name: 'Partly delivered',
      uom: 'KG', available_qty: 40, ordered_qty: 100 }],
    [])
  const r = weightedProcurement(runPlan(m, ['T1']).lines)[0]

  check('the export row carries Total_Procured_Qty at all — the column the '
    + 'Session Report download renders (it was undefined before, which is '
    + 'a blank cell, not an error)',
  r && r.Total_Procured_Qty !== undefined, JSON.stringify(r))
  check('the three exported quantities stay DISTINCT — 40 arrived, 60 still '
    + 'in the pipeline, 100 procured in total',
  r.Available_Qty === 40 && r.Pending_Delivery_Qty === 60
    && r.Total_Procured_Qty === 100,
  `${r.Available_Qty}/${r.Pending_Delivery_Qty}/${r.Total_Procured_Qty}`)
  check('Total Procured is the CEILING max(available, ordered) = 100, NOT the '
    + '140 that available + ordered would give — the subset rule holds in the '
    + 'export layer, not only in the engine',
  r.Total_Procured_Qty === 100 && 40 + 100 === 140,
  `procured=${r.Total_Procured_Qty}`)
  check('it is accumulated from the SPLIT tiers, so it conserves against '
    + 'Allocated_Qty (rule 2) instead of being a fourth independent figure '
    + 'that can drift',
  near(r.Total_Procured_Qty, r.Allocated_Qty),
  `procured=${r.Total_Procured_Qty} allocated=${r.Allocated_Qty}`)
  check('the buy list still nets against the ceiling: demand 100, procured '
    + '100 → nothing left to buy, while 60 KG remain physically short',
  r.Shortfall_Qty === 0 && near(r.Demand_Qty - r.Available_Qty, 60),
  `to buy=${r.Shortfall_Qty} physical gap=${r.Demand_Qty - r.Available_Qty}`)
  check('readiness is untouched by any of this — coverage is TIER 1 only, '
    + '40 of 100 = 40%, never 100% because the order is complete',
  near(r.Fulfillment_Pct, 40), `fulfillment=${r.Fulfillment_Pct}`)
}

console.log(`\n== SME UI MATH: ${failures.length ? '❌ FAIL' : '✅ PASS'} `
  + `(${passed} passed, ${failures.length} failed) ==`)
if (failures.length) {
  console.log('   failed:', failures.join(', '))
  process.exit(1)
}
