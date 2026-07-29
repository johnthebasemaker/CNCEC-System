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
 * Known limit: filter labels come from the RAW field value, so a column whose
 * `render` maps codes to friendly labels (UsersPage's Role → "Head of
 * Department") lists the codes in its dropdown. Give such a column an explicit
 * `filters` array to override.
 */
import { useMemo } from 'react'
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

/** Distinct non-blank values, or null when the column is not filter-worthy. */
function filterOptions<T>(rows: readonly T[], path: Key | readonly Key[]):
    { text: string; value: string }[] | null {
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
    const s = v instanceof Date ? v.toISOString().slice(0, 10) : String(v)
    if (!seen.has(s)) {
      if (seen.size >= MAX_FILTER_OPTIONS) return null
      seen.set(s, s)
    }
  }
  if (seen.size < 2) return null
  return [...seen.keys()].sort(collator.compare).map((s) => ({ text: s, value: s }))
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
    const opts = filterOptions(rows, path)
    if (opts) {
      out.filters = opts
      out.filterSearch = c.filterSearch ?? opts.length >= FILTER_SEARCH_FROM
      out.onFilter = (value, record) => {
        const v = pick(record, path)
        const s = v instanceof Date ? v.toISOString().slice(0, 10) : String(v)
        return !isBlank(v) && s === String(value)
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
 * Drop-in replacement for antd's `Table`. Import it in place of `Table` and the
 * grid gains sorting and filtering with no other change at the call site.
 */
export function Table<T extends object = Record<string, unknown>>(
  { smart, columns, dataSource, pagination, ...rest }: SmartTableProps<T>,
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

  return <AntTable<T> columns={cols} dataSource={dataSource} pagination={pagination} {...rest} />
}

export default Table
