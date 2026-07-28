/**
 * frontend/src/sme/engine.ts — client-side SME allocation engine (Phase S1).
 *
 * Line-for-line mirror of backend/api/sme_engine.py (the Python parity
 * oracle). Both implementations must reproduce backend/api/
 * sme_parity_golden.json exactly — the TS side is checked by
 * frontend/scripts/sme_parity.mjs, the Python side by service_tests.py.
 * If you change ANY numeric behavior here, change the Python module in the
 * same commit and regenerate the golden.
 *
 * All functions are pure: the model comes from GET /sme/model-snapshot and
 * every recalculation (drag-reorder, what-if) runs entirely in the browser.
 */

// ─── Snapshot types (GET /sme/model-snapshot) ────────────────────────────────
export interface SnapshotEquipment {
  Equipment_Tag_No: string
  Name?: string | null
  Location?: string | null
  Sub_Location?: string | null
  Type?: string | null
  Substrate?: string | null
  Lining_System_Code: string | number
  Surface_Area_SQM?: number | string | null
}
export interface SnapshotRecipe {
  Lining_System_Code: string | number
  Lining_System_Name?: string | null
  Material_Code: string
  Material_Name?: string | null
  UOM?: string | null
  For_1_SQM?: number | string | null
}
export interface SnapshotMaterial {
  material_code: string
  material_name?: string | null
  uom?: string | null
  available_qty?: number | string | null
  ordered_qty?: number | string | null
  received_qty?: number | string | null
}
export interface SnapshotProgress {
  Equipment_Tag_No: string
  Lining_System_Code: string | number
  Original_SQM?: number | string | null
  Done_SQM?: number | string | null
  Done_SQM_staged?: number | string | null
}
export interface SmeSnapshot {
  site_id: string | null
  equipment: SnapshotEquipment[]
  recipes: SnapshotRecipe[]
  materials: SnapshotMaterial[]
  progress: SnapshotProgress[]
  default_order: string[]
}

// ─── Engine output types ─────────────────────────────────────────────────────
export interface AllocationLine {
  Equipment_Tag_No: string
  Lining_System_Code: string
  Lining_System_Short_Name: string
  Total_SQM: number
  Material_Code: string
  Material_Name: string
  UOM: string
  For_1_SQM: number
  Demand_Qty: number
  Alloc_Available: number
  Alloc_Ordered: number
  Allocated_Qty: number
  Shortfall_Available_Qty: number
  Shortfall_Qty: number
  Pool_Before: number
  Pool_After: number
  Ordered_Pool_Before: number
  Ordered_Pool_After: number
  Fulfillment_Pct: number
  Fulfillment_With_Ordered_Pct: number
}
export interface FeasibilityRow {
  Priority_Rank: number
  Equipment_Tag_No: string
  Name: string
  Total_Demand_Qty: number
  Total_Allocated_Qty: number
  Total_Alloc_Available: number
  Total_Alloc_Ordered: number
  Total_Shortfall_Qty: number
  Total_Net_Shortfall_Qty: number
  Completion_Pct: number
  Status: string
  Bottleneck_Material_Code: string
  Bottleneck_Material_Name: string
  Bottleneck_Shortfall: number
}
export interface SuggestionRow {
  Pause_Tag: string
  Pause_Name: string
  Newly_Completable_Count: number
  Newly_Completable_Tags: string
  Avg_Completion_Gain_Pct: number
  Net_Gain_Score: number
  Recommended: boolean
}
export interface ProcurementRow {
  Material_Code: string
  Material_Name: string
  UOM: string
  Available_Qty: number
  Ordered_Qty: number
  Gross_Shortfall_Qty: number
  Shortage_Qty_To_Buy: number
}
export interface MaterialTotal {
  Material_Code: string
  Material_Name: string
  UOM: string
  Demand_Qty: number
  Allocated_Qty: number
  Alloc_Available: number
  Alloc_Ordered: number
  Shortfall_Available_Qty: number
  Shortfall_Qty: number
}

/** Reverse-SQM rollup per (tag, system code) — see sme_engine.build_sqm_rollup. */
export interface SqmUnitRow {
  Equipment_Tag_No: string
  Name: string
  Lining_System_Code: string
  System_Name: string
  Total_SQM: number
  Done_SQM: number
  Remaining_SQM: number
  SQM_Achievable_Now: number
  SQM_Achievable_With_Ordered: number
  SQM_Deficit: number
  Coverage_Now_Pct: number
  Has_Recipe: boolean
}
export interface BlockingMaterial {
  Material_Code: string
  Material_Name: string
  UOM: string
  Demand_Qty: number
  Alloc_Available: number
  Alloc_Ordered: number
  Shortfall_Available_Qty: number
  Shortfall_Qty: number
}
export interface SqmCodeRow {
  Lining_System_Code: string
  System_Name: string
  Equipment_Count: number
  Total_SQM: number
  Done_SQM: number
  Remaining_SQM: number
  SQM_Achievable_Now: number
  SQM_Achievable_With_Ordered: number
  SQM_Deficit: number
  Equipment_Tags: string
  Coverage_Now_Pct: number
  Blocking_Materials: BlockingMaterial[]
}
export interface PlanResult {
  order_used: string[]
  lines: AllocationLine[]
  feasibility: FeasibilityRow[]
  totals: MaterialTotal[]
  procurement: ProcurementRow[]
  sqm_units: SqmUnitRow[]
  sqm_by_code: SqmCodeRow[]
}
export interface SuggestionResult {
  suggestions: SuggestionRow[]
  best_detail: (FeasibilityRow & { Scenario: string })[]
}

interface Unit { total_original: number; remaining: number; done: number; short_name: string }
export interface SmeModel {
  units: Map<string, Unit> // key `${tag}\u0000${code}`
  codesByTag: Map<string, string[]>
  recipesByCode: Map<string, { Material_Code: string; Material_Name: string; UOM: string; For_1_SQM: number }[]>
  poolInit: Map<string, number>
  poolOrderedInit: Map<string, number>
  matMeta: Map<string, { Material_Name: string; UOM: string }>
  tagMeta: Map<string, { Name: string; Location: string; Type: string; Substrate: string }>
  defaultOrder: string[]
}

export const STATUS_FULL = '✅ 100% Fully Ready to Build'
export const STATUS_PARTIAL = '🟡 Partially Ready'
export const STATUS_BLOCKED = '🔴 Blocked by Shortages'

/** Half-up rounding shared verbatim with the Python oracle (never diverges). */
export function roundN(x: number, n: number): number {
  if (!Number.isFinite(x)) return 0
  const s = Math.pow(10, n)
  return x < 0 ? -Math.floor(-x * s + 0.5) / s : Math.floor(x * s + 0.5) / s
}

const clip = (x: number, lo: number, hi: number) => (x < lo ? lo : x > hi ? hi : x)

function num(v: unknown): number {
  const f = typeof v === 'number' ? v : parseFloat(String(v ?? ''))
  return Number.isNaN(f) ? 0 : f
}

const s = (v: unknown): string => (v === null || v === undefined ? '' : String(v).trim())

const ukey = (tag: string, code: string) => `${tag}\u0000${code}`

/** Public unit-map key for consumers like insights.ts (no numeric behavior). */
export const unitKey = ukey

/** Numeric-first ordering for lining-system codes (mirrors syscode_sort_key). */
export function syscodeCompare(a: string, b: string): number {
  const ad = /^\d+$/.test(a)
  const bd = /^\d+$/.test(b)
  if (ad && bd) return Number(a) - Number(b)
  if (ad !== bd) return ad ? -1 : 1
  return a < b ? -1 : a > b ? 1 : 0
}

/** Code-point string compare (mirrors Python's str ordering, not locale). */
const strCompare = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

// ─── Model ───────────────────────────────────────────────────────────────────
export function buildModel(
  equipment: SnapshotEquipment[], recipes: SnapshotRecipe[],
  materials: SnapshotMaterial[], progress: SnapshotProgress[],
): SmeModel {
  const recipesByCode: SmeModel['recipesByCode'] = new Map()
  const shortNameByCode = new Map<string, string>()
  for (const r of recipes) {
    const code = s(r.Lining_System_Code)
    const row = {
      Material_Code: s(r.Material_Code), Material_Name: s(r.Material_Name),
      UOM: s(r.UOM), For_1_SQM: num(r.For_1_SQM),
    }
    if (!recipesByCode.has(code)) recipesByCode.set(code, [])
    recipesByCode.get(code)!.push(row)
    if (!shortNameByCode.has(code)) shortNameByCode.set(code, s(r.Lining_System_Name))
  }

  const prog = new Map<string, { original: number; done: number; remaining: number }>()
  for (const p of progress) {
    const orig = num(p.Original_SQM)
    const done = num(p.Done_SQM) + num(p.Done_SQM_staged)
    prog.set(ukey(s(p.Equipment_Tag_No), s(p.Lining_System_Code)),
      { original: orig, done, remaining: Math.max(orig - done, 0) })
  }

  const units: SmeModel['units'] = new Map()
  const tagMeta: SmeModel['tagMeta'] = new Map()
  const codesByTag: SmeModel['codesByTag'] = new Map()
  for (const e of equipment) {
    const tag = s(e.Equipment_Tag_No)
    const code = s(e.Lining_System_Code)
    if (!tag) continue
    if (!tagMeta.has(tag)) {
      tagMeta.set(tag, {
        Name: s(e.Name), Location: s(e.Location), Type: s(e.Type), Substrate: s(e.Substrate),
      })
      codesByTag.set(tag, [])
    }
    const k = ukey(tag, code)
    const u = units.get(k)
    if (u === undefined) {
      units.set(k, { total_original: num(e.Surface_Area_SQM), remaining: 0, done: 0, short_name: '' })
      codesByTag.get(tag)!.push(code)
    } else {
      u.total_original += num(e.Surface_Area_SQM)
    }
  }
  for (const [k, u] of units) {
    const p = prog.get(k)
    u.remaining = p !== undefined ? p.remaining : u.total_original
    u.done = p !== undefined ? p.done : 0
    u.short_name = shortNameByCode.get(k.split('\u0000')[1]) ?? ''
  }
  for (const codes of codesByTag.values()) codes.sort(syscodeCompare)

  const poolInit = new Map<string, number>()
  const poolOrderedInit = new Map<string, number>()
  const matMeta: SmeModel['matMeta'] = new Map()
  for (const m of materials) {
    const mat = s(m.material_code)
    poolInit.set(mat, num(m.available_qty))
    // 2026-07-28 EFFECTIVE-ORDERED ruling (Q2a) — mirrors sme_engine.py.
    // ordered_qty is a static workbook snapshot never decremented on delivery;
    // arrivals land as receipts, which already inflate available_qty. Netting
    // receipts off the order stops delivered stock being counted twice.
    const effOrdered = num(m.ordered_qty) - num(m.received_qty)
    poolOrderedInit.set(mat, effOrdered > 0 ? effOrdered : 0)
    matMeta.set(mat, { Material_Name: s(m.material_name), UOM: s(m.uom) })
  }

  return {
    units, codesByTag, recipesByCode, poolInit, poolOrderedInit, matMeta, tagMeta,
    defaultOrder: [...codesByTag.keys()].sort(strCompare),
  }
}

function dedupe(order: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of order) {
    const t = s(raw)
    if (t && !seen.has(t)) { seen.add(t); out.push(t) }
  }
  return out
}

// ─── Cascade allocation (legacy cascade_allocate port) ───────────────────────
export function cascadeAllocate(model: SmeModel, order: string[]): AllocationLine[] {
  const pool = new Map(model.poolInit)
  const poolOrdered = new Map(model.poolOrderedInit)
  const lines: AllocationLine[] = []
  const raw: { demand: number; avail: number }[] = []
  // ── pass 1: PHYSICAL stock, priority order (identical to the single-tier
  //    cascade this replaces, so every historical field keeps its value) ────
  for (const tag of dedupe(order)) {
    for (const code of model.codesByTag.get(tag) ?? []) {
      const unit = model.units.get(ukey(tag, code))!
      const remaining = unit.remaining
      for (const r of model.recipesByCode.get(code) ?? []) {
        const mat = r.Material_Code
        const demand = r.For_1_SQM * remaining
        const before = pool.get(mat) ?? 0
        const alloc = Math.min(demand, before)
        const after = Math.max(0, before - alloc)
        pool.set(mat, after)
        const d4 = roundN(demand, 4)
        const a4 = roundN(alloc, 4)
        raw.push({ demand, avail: alloc })
        lines.push({
          Equipment_Tag_No: tag,
          Lining_System_Code: code,
          Lining_System_Short_Name: unit.short_name,
          Total_SQM: roundN(remaining, 2),
          Material_Code: mat,
          Material_Name: r.Material_Name || (model.matMeta.get(mat)?.Material_Name ?? ''),
          UOM: r.UOM,
          For_1_SQM: r.For_1_SQM,
          Demand_Qty: d4,
          Alloc_Available: a4,
          Alloc_Ordered: 0,
          Allocated_Qty: a4,
          Shortfall_Available_Qty: roundN(demand - alloc, 4),
          Shortfall_Qty: roundN(demand - alloc, 4),
          Pool_Before: roundN(before, 4),
          Pool_After: roundN(after, 4),
          Ordered_Pool_Before: 0,
          Ordered_Pool_After: 0,
          Fulfillment_Pct: d4 > 0 ? roundN(clip((a4 / d4) * 100, 0, 100), 2) : 100,
          Fulfillment_With_Ordered_Pct: d4 > 0 ? roundN(clip((a4 / d4) * 100, 0, 100), 2) : 100,
        })
      }
    }
  }
  // ── pass 2: ON-ORDER stock against the remaining gap, same priority walk ─
  for (let i = 0; i < lines.length; i += 1) {
    const ln = lines[i]
    const mat = ln.Material_Code
    const gap = raw[i].demand - raw[i].avail
    const before = poolOrdered.get(mat) ?? 0
    const alloc = gap > 0 ? Math.min(gap, before) : 0
    const after = Math.max(0, before - alloc)
    poolOrdered.set(mat, after)
    const total = raw[i].avail + alloc
    const d4 = ln.Demand_Qty
    const t4 = roundN(total, 4)
    ln.Alloc_Ordered = roundN(alloc, 4)
    ln.Allocated_Qty = t4
    ln.Shortfall_Qty = roundN(raw[i].demand - total, 4)
    ln.Ordered_Pool_Before = roundN(before, 4)
    ln.Ordered_Pool_After = roundN(after, 4)
    ln.Fulfillment_With_Ordered_Pct = d4 > 0 ? roundN(clip((t4 / d4) * 100, 0, 100), 2) : 100
  }
  return lines
}

// ─── Feasibility (legacy compute_feasibility port, cascade granularity) ──────
export function computeFeasibility(
  model: SmeModel, lines: AllocationLine[], order: string[],
): FeasibilityRow[] {
  const byTag = new Map<string, AllocationLine[]>()
  for (const ln of lines) {
    if (!byTag.has(ln.Equipment_Tag_No)) byTag.set(ln.Equipment_Tag_No, [])
    byTag.get(ln.Equipment_Tag_No)!.push(ln)
  }

  const out: FeasibilityRow[] = []
  let rank = 0
  for (const tag of dedupe(order)) {
    rank += 1
    const rows = byTag.get(tag)
    if (!rows || rows.length === 0) continue
    // "Ready to Build" is a PHYSICAL claim: stock still on order cannot be
    // applied to a tank today, so feasibility judges tier 1 only (which also
    // keeps every historical value intact). Mirrors sme_engine.py.
    let demand = 0, alloc = 0, allocAv = 0, allocOr = 0, short = 0, shortNet = 0
    for (const r of rows) {
      demand += r.Demand_Qty
      alloc += r.Allocated_Qty
      allocAv += r.Alloc_Available
      allocOr += r.Alloc_Ordered
      short += r.Shortfall_Available_Qty
      shortNet += r.Shortfall_Qty
    }
    let minRate = 2
    let bottleneck: AllocationLine | null = null
    for (const r of rows) {
      const rate = r.Demand_Qty > 0 ? clip(r.Alloc_Available / r.Demand_Qty, 0, 1) : 1
      if (rate < minRate) { minRate = rate; bottleneck = r } // strict: first min wins ties
    }
    // 2026-07-07 STRICT BOTTLENECK ruling (mirrors sme_engine.py): coverage =
    // the LEAST-available material's rate, never the Σalloc/Σdemand average.
    const completion = demand > 0 ? roundN(clip(minRate * 100, 0, 100), 2) : 100
    const status = short <= 0 ? STATUS_FULL
      : minRate === 0 ? STATUS_BLOCKED
        : `${STATUS_PARTIAL} (${completion.toFixed(1)}%)`
    const hasBn = bottleneck !== null && bottleneck.Shortfall_Available_Qty > 0
    out.push({
      Priority_Rank: rank,
      Equipment_Tag_No: tag,
      Name: model.tagMeta.get(tag)?.Name ?? '',
      Total_Demand_Qty: roundN(demand, 4),
      Total_Allocated_Qty: roundN(alloc, 4),
      Total_Alloc_Available: roundN(allocAv, 4),
      Total_Alloc_Ordered: roundN(allocOr, 4),
      Total_Shortfall_Qty: roundN(short, 4),
      Total_Net_Shortfall_Qty: roundN(shortNet, 4),
      Completion_Pct: completion,
      Status: status,
      Bottleneck_Material_Code: hasBn ? bottleneck!.Material_Code : '—',
      Bottleneck_Material_Name: hasBn ? bottleneck!.Material_Name : '—',
      Bottleneck_Shortfall: hasBn ? bottleneck!.Shortfall_Available_Qty : 0,
    })
  }
  return out
}

// ─── Suggestion engine (legacy run_suggestion_engine port) ───────────────────
export function runSuggestionEngine(model: SmeModel, orderIn: string[]): SuggestionResult {
  const order = dedupe(orderIn)
  const baseFeas = computeFeasibility(model, cascadeAllocate(model, order), order)
  const baseFull = new Set(baseFeas.filter((f) => f.Status === STATUS_FULL).map((f) => f.Equipment_Tag_No))
  const candidates = baseFeas.filter((f) => f.Status !== STATUS_FULL).map((f) => f.Equipment_Tag_No)

  const rows: SuggestionRow[] = []
  let bestScore: [number, number] = [-1, -999]
  let bestDetail: SuggestionResult['best_detail'] = []
  for (const pause of candidates) {
    const simOrder = order.filter((t) => t !== pause)
    const simFeas = computeFeasibility(model, cascadeAllocate(model, simOrder), simOrder)
    const simFull = new Set(simFeas.filter((f) => f.Status === STATUS_FULL).map((f) => f.Equipment_Tag_No))
    const simCompletion = new Map(simFeas.map((f) => [f.Equipment_Tag_No, f.Completion_Pct]))
    const newly = [...simFull].filter((t) => !baseFull.has(t)).sort(strCompare)
    const gains: number[] = []
    for (const f of baseFeas) {
      if (f.Equipment_Tag_No !== pause && simCompletion.has(f.Equipment_Tag_No)) {
        gains.push(simCompletion.get(f.Equipment_Tag_No)! - f.Completion_Pct)
      }
    }
    const avgGain = gains.length ? gains.reduce((a, b) => a + b, 0) / gains.length : 0
    rows.push({
      Pause_Tag: pause,
      Pause_Name: model.tagMeta.get(pause)?.Name || pause,
      Newly_Completable_Count: newly.length,
      Newly_Completable_Tags: newly.length ? newly.join(', ') : '—',
      Avg_Completion_Gain_Pct: roundN(avgGain, 2),
      Net_Gain_Score: newly.length - 1,
      Recommended: false,
    })
    if (newly.length > bestScore[0] || (newly.length === bestScore[0] && avgGain > bestScore[1])) {
      bestScore = [newly.length, avgGain]
      bestDetail = simFeas.map((f) => ({ ...f, Scenario: `If '${pause}' is paused` }))
    }
  }

  rows.sort((a, b) => b.Newly_Completable_Count - a.Newly_Completable_Count
    || b.Avg_Completion_Gain_Pct - a.Avg_Completion_Gain_Pct) // stable on ties
  if (rows.length) rows[0].Recommended = true
  return { suggestions: rows, best_detail: bestDetail }
}

// ─── Procurement list + per-material totals ──────────────────────────────────
export function buildProcurementList(model: SmeModel, lines: AllocationLine[]): ProcurementRow[] {
  // Keyed on the NET shortfall, so stock already on order is not re-ordered.
  const shortage = new Map<string, number>()
  const gross = new Map<string, number>()
  for (const ln of lines) {
    shortage.set(ln.Material_Code, (shortage.get(ln.Material_Code) ?? 0) + ln.Shortfall_Qty)
    gross.set(ln.Material_Code, (gross.get(ln.Material_Code) ?? 0) + ln.Shortfall_Available_Qty)
  }
  const out: ProcurementRow[] = []
  for (const mat of [...shortage.keys()].sort(strCompare)) {
    const v = shortage.get(mat)!
    if (v <= 0) continue
    const meta = model.matMeta.get(mat)
    out.push({
      Material_Code: mat,
      Material_Name: meta?.Material_Name ?? '',
      UOM: meta?.UOM ?? '',
      Available_Qty: model.poolInit.get(mat) ?? 0,
      Ordered_Qty: model.poolOrderedInit.get(mat) ?? 0,
      Gross_Shortfall_Qty: roundN(gross.get(mat) ?? 0, 3),
      Shortage_Qty_To_Buy: roundN(v, 3),
    })
  }
  out.sort((a, b) => b.Shortage_Qty_To_Buy - a.Shortage_Qty_To_Buy
    || strCompare(a.Material_Code, b.Material_Code))
  return out
}

export function buildTotals(lines: AllocationLine[]): MaterialTotal[] {
  const totals = new Map<string, MaterialTotal>()
  for (const ln of lines) {
    let t = totals.get(ln.Material_Code)
    if (t === undefined) {
      t = {
        Material_Code: ln.Material_Code, Material_Name: ln.Material_Name, UOM: ln.UOM,
        Demand_Qty: 0, Allocated_Qty: 0, Alloc_Available: 0, Alloc_Ordered: 0,
        Shortfall_Available_Qty: 0, Shortfall_Qty: 0,
      }
      totals.set(ln.Material_Code, t)
    }
    t.Demand_Qty += ln.Demand_Qty
    t.Allocated_Qty += ln.Allocated_Qty
    t.Alloc_Available += ln.Alloc_Available
    t.Alloc_Ordered += ln.Alloc_Ordered
    t.Shortfall_Available_Qty += ln.Shortfall_Available_Qty
    t.Shortfall_Qty += ln.Shortfall_Qty
  }
  return [...totals.keys()].sort(strCompare).map((mat) => {
    const t = totals.get(mat)!
    return {
      ...t,
      Demand_Qty: roundN(t.Demand_Qty, 3),
      Allocated_Qty: roundN(t.Allocated_Qty, 3),
      Alloc_Available: roundN(t.Alloc_Available, 3),
      Alloc_Ordered: roundN(t.Alloc_Ordered, 3),
      Shortfall_Available_Qty: roundN(t.Shortfall_Available_Qty, 3),
      Shortfall_Qty: roundN(t.Shortfall_Qty, 3),
    }
  })
}

// ─── Reverse SQM: bottleneck-limited achievable area (2026-07-28) ────────────
/** Mirrors sme_engine._achievable — see there for the full ruling rationale. */
function achievable(unitLines: AllocationLine[], field: 'Alloc_Available' | 'Allocated_Qty',
                    remaining: number): number {
  const rates = unitLines.filter((ln) => ln.For_1_SQM > 0)
  if (rates.length === 0) return 0   // ruling Q5: unmodelled is never 100%
  let best = Infinity
  for (const ln of rates) {
    const v = ln[field] / ln.For_1_SQM
    if (v < best) best = v
  }
  return clip(best, 0, remaining)
}

/** Mirrors sme_engine.build_sqm_rollup. Units come from codesByTag, not from
 *  `lines`, so a code with no recipe still appears (with 0 achievable). */
export function buildSqmRollup(
  model: SmeModel, lines: AllocationLine[], order: string[],
): SqmUnitRow[] {
  const byUnit = new Map<string, AllocationLine[]>()
  for (const ln of lines) {
    const k = ukey(ln.Equipment_Tag_No, ln.Lining_System_Code)
    if (!byUnit.has(k)) byUnit.set(k, [])
    byUnit.get(k)!.push(ln)
  }
  const out: SqmUnitRow[] = []
  for (const tag of dedupe(order)) {
    for (const code of model.codesByTag.get(tag) ?? []) {
      const unit = model.units.get(ukey(tag, code))
      if (unit === undefined) continue
      const remaining = unit.remaining
      const ul = byUnit.get(ukey(tag, code)) ?? []
      const now = achievable(ul, 'Alloc_Available', remaining)
      const withOrd = achievable(ul, 'Allocated_Qty', remaining)
      out.push({
        Equipment_Tag_No: tag,
        Name: model.tagMeta.get(tag)?.Name ?? '',
        Lining_System_Code: code,
        System_Name: unit.short_name,
        Total_SQM: roundN(unit.total_original, 2),
        Done_SQM: roundN(unit.done, 2),
        Remaining_SQM: roundN(remaining, 2),
        SQM_Achievable_Now: roundN(now, 2),
        SQM_Achievable_With_Ordered: roundN(withOrd, 2),
        SQM_Deficit: roundN(remaining - now, 2),
        Coverage_Now_Pct: remaining > 0 ? roundN(clip((now / remaining) * 100, 0, 100), 2) : 100,
        Has_Recipe: ul.length > 0,
      })
    }
  }
  return out
}

/** Mirrors sme_engine.build_sqm_by_code. SQM is ADDITIVE across equipment —
 *  never average the coverage rates. */
export function buildSqmByCode(
  lines: AllocationLine[], rollup: SqmUnitRow[],
): SqmCodeRow[] {
  const SUM = ['Total_SQM', 'Done_SQM', 'Remaining_SQM', 'SQM_Achievable_Now',
    'SQM_Achievable_With_Ordered', 'SQM_Deficit'] as const
  const agg = new Map<string, SqmCodeRow & { _tags: string[] }>()
  for (const r of rollup) {
    let a = agg.get(r.Lining_System_Code)
    if (a === undefined) {
      a = {
        Lining_System_Code: r.Lining_System_Code, System_Name: r.System_Name,
        Equipment_Count: 0, Total_SQM: 0, Done_SQM: 0, Remaining_SQM: 0,
        SQM_Achievable_Now: 0, SQM_Achievable_With_Ordered: 0, SQM_Deficit: 0,
        Equipment_Tags: '', Coverage_Now_Pct: 0, Blocking_Materials: [], _tags: [],
      }
      agg.set(r.Lining_System_Code, a)
    }
    a.Equipment_Count += 1
    a._tags.push(r.Equipment_Tag_No)
    for (const f of SUM) a[f] += r[f]
  }
  const block = new Map<string, Map<string, BlockingMaterial>>()
  const BSUM = ['Demand_Qty', 'Alloc_Available', 'Alloc_Ordered',
    'Shortfall_Available_Qty', 'Shortfall_Qty'] as const
  for (const ln of lines) {
    if (!block.has(ln.Lining_System_Code)) block.set(ln.Lining_System_Code, new Map())
    const m = block.get(ln.Lining_System_Code)!
    let b = m.get(ln.Material_Code)
    if (b === undefined) {
      b = {
        Material_Code: ln.Material_Code, Material_Name: ln.Material_Name, UOM: ln.UOM,
        Demand_Qty: 0, Alloc_Available: 0, Alloc_Ordered: 0,
        Shortfall_Available_Qty: 0, Shortfall_Qty: 0,
      }
      m.set(ln.Material_Code, b)
    }
    for (const f of BSUM) b[f] += ln[f]
  }
  const out: SqmCodeRow[] = []
  for (const code of [...agg.keys()].sort(syscodeCompare)) {
    const a = agg.get(code)!
    const rem = a.Remaining_SQM
    const mats = [...(block.get(code)?.values() ?? [])]
      .filter((m) => m.Shortfall_Available_Qty > 0)
      .map((m) => {
        const o = { ...m }
        for (const f of BSUM) o[f] = roundN(o[f], 4)
        return o
      })
    mats.sort((x, y) => y.Shortfall_Qty - x.Shortfall_Qty
      || strCompare(x.Material_Code, y.Material_Code))
    const row: SqmCodeRow = {
      Lining_System_Code: a.Lining_System_Code, System_Name: a.System_Name,
      Equipment_Count: a.Equipment_Count,
      Total_SQM: roundN(a.Total_SQM, 2), Done_SQM: roundN(a.Done_SQM, 2),
      Remaining_SQM: roundN(a.Remaining_SQM, 2),
      SQM_Achievable_Now: roundN(a.SQM_Achievable_Now, 2),
      SQM_Achievable_With_Ordered: roundN(a.SQM_Achievable_With_Ordered, 2),
      SQM_Deficit: roundN(a.SQM_Deficit, 2),
      Equipment_Tags: a._tags.join(', '),
      Coverage_Now_Pct: rem > 0
        ? roundN(clip((a.SQM_Achievable_Now / rem) * 100, 0, 100), 2) : 100,
      Blocking_Materials: mats,
    }
    out.push(row)
  }
  return out
}

/** One-shot plan: cascade + feasibility + totals + procurement. */
export function runPlan(model: SmeModel, orderIn: string[]): PlanResult {
  const order = dedupe(orderIn)
  const lines = cascadeAllocate(model, order)
  const rollup = buildSqmRollup(model, lines, order)
  return {
    order_used: order,
    lines,
    feasibility: computeFeasibility(model, lines, order),
    totals: buildTotals(lines),
    procurement: buildProcurementList(model, lines),
    sqm_units: rollup,
    sqm_by_code: buildSqmByCode(lines, rollup),
  }
}
