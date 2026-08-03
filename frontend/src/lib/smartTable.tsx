/**
 * frontend/src/lib/smartTable.tsx — project-wide table sorting + filtering.
 *
 * Every data grid in the app (99 `<Table>` instances across 45 files, the SME
 * portal included) renders through this wrapper instead of antd's `Table`
 * directly. It derives a sorter for every column that maps to a field, and a
 * checkbox filter for every column that turns out to be categorical, WITHOUT
 * touching a single column definition or adding a pixel of chrome — the affordances
 * live entirely in the header cells antd already draws.
 *
 * Two deliberate limits keep the UI clean rather than cluttered:
 *   • filters appear only on low-cardinality, non-numeric columns. A quantity or
 *     a 500-value material name gets a sorter and no dropdown; a status, site or
 *     system code gets both.
 *   • server-paginated tables are left alone. Sorting one page of 20 out of 5,000
 *     server-side rows looks like it works and silently lies, so `Table` sniffs
 *     controlled pagination (`total` + `current` both set) and opts out.
 *
 * A call site can force the decision either way with `smart={true|false}`, and an
 * explicit `sorter`/`filters` on a column is always left untouched.
 *
 * Filter LABELS follow the cell, not the column's raw field: a column whose
 * `render` turns `hod` into "Head of Department" lists "Head of Department" in
 * its dropdown, while the checkbox still filters on the raw value. The label is
 * read out of the rendered node (see `nodeText`) and is trusted only when it is
 * stable and unambiguous — a value that renders differently row to row, or two
 * values that render the same text, fall back to the raw string rather than
 * mislabel a checkbox. An explicit `filters` array still overrides everything.
 */
import { isValidElement, useEffect, useMemo, useState } from 'react'
import type { Key, ReactNode } from 'react'
import { Table as AntTable } from 'antd'
import type { TableProps } from 'antd'
import type { ColumnGroupType, ColumnType, ColumnsType } from 'antd/es/table'

type AnyCol<T> = ColumnGroupType<T> | ColumnType<T>

/** Max distinct values a column may hold and still be offered as a filter. */
const MAX_FILTER_OPTIONS = 30
/** From this many options up, the filter dropdown grows a search box. */
const FILTER_SEARCH_FROM = 8
/** Rows sampled when deciding whether a column is categorical. */
const SAMPLE = 400
/** Occurrences of a value inspected before its rendered label is trusted. */
const LABEL_CONFIRM = 2
/** Total `render` calls a column may cost while deriving its filter labels. */
const LABEL_BUDGET = MAX_FILTER_OPTIONS * LABEL_CONFIRM
/** Longer than this and the cell is prose, not a label — keep the raw value. */
const LABEL_MAX_LEN = 80
/** Depth guard for walking a rendered cell; real cells nest a handful deep. */
const LABEL_MAX_DEPTH = 6
/** Props a component may carry its text in when it has no children. */
const LABEL_PROPS = ['text', 'title', 'label'] as const

// Numeric-aware so "10" sorts after "9" and "TK-2" after "TK-10" reads naturally.
const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' })

function pick(record: unknown, path: Key | readonly Key[]): unknown {
  if (record === null || typeof record !== 'object') return undefined
  if (!Array.isArray(path)) return (record as Record<string, unknown>)[String(path)]
  let cur: unknown = record
  for (const seg of path as readonly Key[]) {
    if (cur === null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[String(seg)]
  }
  return cur
}

function isBlank(v: unknown): boolean {
  return v === null || v === undefined || v === '' || (typeof v === 'number' && Number.isNaN(v))
}

/** The string a raw cell value is keyed and filtered by. */
function asKey(v: unknown): string {
  return v instanceof Date ? v.toISOString().slice(0, 10) : String(v)
}

/**
 * Readable text of a rendered cell, without mounting it. Handles the shapes our
 * columns actually return: plain strings, `<Tag>label</Tag>`, fragments and
 * spans mixing an icon with text, antd's `{ children, props }` cell wrapper, and
 * components that carry their text in a prop (`<Badge text=… />`).
 */
function nodeText(node: unknown, depth = 0): string {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string') return node
  if (typeof node === 'number') return String(node)
  if (depth >= LABEL_MAX_DEPTH) return ''
  if (Array.isArray(node)) return node.map((n) => nodeText(n, depth + 1)).join(' ')
  if (isValidElement(node)) {
    const props = (node.props ?? {}) as Record<string, unknown>
    const kids = nodeText(props.children, depth + 1)
    if (kids.trim()) return kids
    for (const p of LABEL_PROPS) {
      const val = props[p]
      if (typeof val === 'string' || typeof val === 'number') return String(val)
    }
    return ''
  }
  // A render may return { children, props } to drive colSpan/rowSpan.
  if (typeof node === 'object' && 'children' in (node as object)) {
    return nodeText((node as { children: unknown }).children, depth + 1)
  }
  return ''
}

type CellRender<T> = NonNullable<ColumnType<T>['render']>

/**
 * Map each raw value to the label its cell renders, keeping only the mappings
 * that are safe to show in a checkbox list. A value is dropped when the render
 * throws, yields no text, or disagrees with itself across rows (a label that
 * depends on the whole record, not the field); a label shared by two different
 * values is dropped from both, since two identically-named checkboxes filtering
 * different rows is worse than showing the codes.
 */
function derivedLabels<T>(
  rows: readonly T[], path: Key | readonly Key[], render: CellRender<T>,
): Map<string, string> {
  const found = new Map<string, string | null>()
  const checks = new Map<string, number>()
  const n = Math.min(rows.length, SAMPLE)
  let budget = LABEL_BUDGET
  for (let i = 0; i < n && budget > 0; i += 1) {
    const v = pick(rows[i], path)
    if (isBlank(v)) continue
    const s = asKey(v)
    const done = checks.get(s) ?? 0
    if (done >= LABEL_CONFIRM) continue
    checks.set(s, done + 1)
    budget -= 1

    let text = ''
    try {
      text = nodeText(render(v, rows[i], i)).replace(/\s+/g, ' ').trim()
    } catch {
      text = '' // a render that needs more context than we can give it
    }
    const usable = text && text.length <= LABEL_MAX_LEN ? text : null
    const prev = found.get(s)
    found.set(s, prev === undefined ? usable : (prev === usable ? prev : null))
  }

  const byLabel = new Map<string, number>()
  for (const label of found.values()) {
    if (label) byLabel.set(label, (byLabel.get(label) ?? 0) + 1)
  }
  const out = new Map<string, string>()
  for (const [value, label] of found) {
    if (label && label !== value && byLabel.get(label) === 1) out.set(value, label)
  }
  return out
}

/**
 * One comparator for every column. Blanks sort to the top ascending (and so to
 * the bottom descending), which is what you want when you click a shortfall
 * column looking for the worst offenders.
 */
export function compareValues(a: unknown, b: unknown): number {
  if (isBlank(a) || isBlank(b)) return isBlank(a) && isBlank(b) ? 0 : isBlank(a) ? -1 : 1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  if (typeof a === 'boolean' && typeof b === 'boolean') return Number(a) - Number(b)
  if (a instanceof Date && b instanceof Date) return a.getTime() - b.getTime()
  return collator.compare(String(a), String(b))
}

/**
 * Distinct non-blank values labelled the way their cells read, or null when the
 * column is not filter-worthy.
 */
function filterOptions<T>(
  rows: readonly T[], path: Key | readonly Key[], render?: CellRender<T>,
): { text: string; value: string }[] | null {
  const seen = new Map<string, string>()
  const n = Math.min(rows.length, SAMPLE)
  for (let i = 0; i < n; i += 1) {
    const v = pick(rows[i], path)
    if (isBlank(v)) continue
    // A numeric column is a measurement, not a category — a dropdown of 28
    // distinct quantities is noise. Objects/JSX have no sane checkbox label.
    // Booleans are excluded too: a two-option dropdown reading "true/false"
    // rarely matches what the cell renders ("on"/"off", a ✓, a coloured tag),
    // and the sorter already groups them.
    if (typeof v === 'number' || typeof v === 'boolean'
        || (typeof v === 'object' && !(v instanceof Date))) return null
    const s = asKey(v)
    if (!seen.has(s)) {
      if (seen.size >= MAX_FILTER_OPTIONS) return null
      seen.set(s, s)
    }
  }
  if (seen.size < 2) return null

  // Label each option the way its cell reads. Only now, with the column known
  // to be categorical, is it worth calling `render` at all.
  const labels = render ? derivedLabels(rows, path, render) : null
  return [...seen.keys()]
    .map((s) => ({ text: labels?.get(s) ?? s, value: s }))
    .sort((a, b) => collator.compare(a.text, b.text))
}

/**
 * Per-dataset memo for `filterOptions`, which is the only expensive thing this
 * module does: it scans up to SAMPLE rows per column and, for a categorical
 * column, calls the cell `render` once per row to label the options.
 *
 * Why it is needed: 28 of the 33 `<Table>` call sites build their `columns`
 * array inline in the component body, so the array has a new identity on every
 * render and the `useMemo` in `Table` below never hits. Without this cache
 * those row scans re-ran on every keystroke, tab switch and poll tick.
 *
 * Keyed on the ROWS ARRAY IDENTITY (WeakMap, so a replaced dataset is collected
 * with its cache) plus the column path. Deliberately NOT keyed on the `render`
 * function, which is a fresh closure each render but maps a given raw value to
 * the same label. The bounded consequence: if a render's output depends on
 * state outside the row data, a filter dropdown can show the previous LABELS
 * until the dataset changes. Filtering itself is unaffected — `onFilter`
 * compares the raw value and is rebuilt every render.
 */
const _filterCache = new WeakMap<object, Map<string, { text: string; value: string }[] | null>>()

function cachedFilterOptions<T>(
  rows: readonly T[], path: Key | readonly Key[], render?: CellRender<T>,
): { text: string; value: string }[] | null {
  if (!Array.isArray(rows) || rows.length === 0) return filterOptions(rows, path, render)
  let perDataset = _filterCache.get(rows as unknown as object)
  if (!perDataset) {
    perDataset = new Map()
    _filterCache.set(rows as unknown as object, perDataset)
  }
  const key = Array.isArray(path) ? path.join(' ') : String(path)
  if (perDataset.has(key)) return perDataset.get(key) ?? null
  const opts = filterOptions(rows, path, render)
  perDataset.set(key, opts)
  return opts
}

function enhance<T>(col: AnyCol<T>, rows: readonly T[]): AnyCol<T> {
  if ('children' in col && Array.isArray(col.children)) {
    return { ...col, children: col.children.map((c) => enhance(c as AnyCol<T>, rows)) } as AnyCol<T>
  }
  const c = col as ColumnType<T>
  const path = c.dataIndex as Key | readonly Key[] | undefined
  // No dataIndex means a computed/action column — there is nothing to sort by.
  if (path === undefined || path === null) return col

  const out: ColumnType<T> = { ...c }
  if (c.sorter === undefined) {
    out.sorter = (a: T, b: T) => compareValues(pick(a, path), pick(b, path))
    out.sortDirections = c.sortDirections ?? ['ascend', 'descend']
    out.showSorterTooltip = c.showSorterTooltip ?? false
  }
  if (c.filters === undefined && c.filterDropdown === undefined) {
    const opts = cachedFilterOptions(rows, path, c.render)
    if (opts) {
      out.filters = opts
      out.filterSearch = c.filterSearch ?? opts.length >= FILTER_SEARCH_FROM
      // Labels follow the cell; the checkbox still filters on the raw value.
      out.onFilter = (value, record) => {
        const v = pick(record, path)
        return !isBlank(v) && asKey(v) === String(value)
      }
    }
  }
  return out
}

/**
 * Return `columns` with a sorter (and, where the data is categorical, a filter)
 * attached to every column that maps to a field. Explicit config always wins.
 */
export function smartColumns<T>(columns: ColumnsType<T>, rows: readonly T[]): ColumnsType<T> {
  return columns.map((c) => enhance(c as AnyCol<T>, rows))
}

export type SmartTableProps<T> = TableProps<T> & {
  /** Force enhancement on or off. Default: on unless pagination is server-driven. */
  smart?: boolean
}

/**
 * The app header's height, which is what a viewport-sticky table header has to
 * clear. It lives in CSS (`--gi-header-h`) because it is breakpoint-dependent,
 * and call sites hard-coded `offsetHeader: 64` — correct on desktop, 8px too
 * far down on a phone, which opened a gap that rows showed through.
 */
function readHeaderOffset(): number {
  if (typeof window === 'undefined') return DEFAULT_HEADER_OFFSET
  const v = getComputedStyle(document.documentElement).getPropertyValue('--gi-header-h')
  const n = Number.parseInt(v, 10)
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_HEADER_OFFSET
}

const DEFAULT_HEADER_OFFSET = 64

function useHeaderOffset(): number {
  const [h, setH] = useState(readHeaderOffset)
  useEffect(() => {
    const onResize = () => setH(readHeaderOffset())
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  return h
}

/**
 * Drop-in replacement for antd's `Table`. Import it in place of `Table` and the
 * grid gains sorting and filtering with no other change at the call site.
 */
export function Table<T extends object = Record<string, unknown>>(
  { smart, columns, dataSource, pagination, sticky, scroll, ...rest }: SmartTableProps<T>,
): ReactNode {
  // Controlled `total` + `current` means the server owns paging: only the
  // current page is in memory, so client-side sort/filter would mislead.
  const serverPaged = typeof pagination === 'object' && pagination !== null
    && pagination.total !== undefined && pagination.current !== undefined
  const enabled = smart ?? !serverPaged

  const cols = useMemo(
    () => (enabled && columns ? smartColumns(columns, dataSource ?? []) : columns),
    [enabled, columns, dataSource],
  )

  // Sticky headers, normalised in ONE place instead of 95 call sites.
  //
  // 1. `scroll.y` and `sticky` are mutually exclusive. With a body height antd
  //    already pins the header inside the table's own scroll box; adding
  //    viewport-sticky on top detaches that header and floats it into the
  //    MIDDLE of the scrolling body — rows stay visible above it and slide
  //    under it. That is the portrait-mode overlap. The header stays sticky
  //    either way; this just picks the mechanism that owns it.
  // 2. Otherwise the offset follows the real header height rather than a
  //    hard-coded 64.
  const headerOffset = useHeaderOffset()
  const stickyProp = useMemo(() => {
    if (scroll?.y !== undefined && scroll?.y !== null) return undefined
    if (sticky === false || sticky === undefined) return sticky
    const base = sticky === true ? {} : sticky
    return { ...base, offsetHeader: headerOffset }
  }, [sticky, scroll?.y, headerOffset])

  return (
    <AntTable<T> columns={cols} dataSource={dataSource} pagination={pagination}
      sticky={stickyProp} scroll={scroll} {...rest} />
  )
}

export default Table
