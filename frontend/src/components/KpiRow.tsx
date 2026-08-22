import { Children, isValidElement } from 'react'
import type { CSSProperties, ReactNode } from 'react'

interface Props {
  children: ReactNode
  /**
   * The narrowest a card may be before the row wraps. Cards then divide the
   * FULL width evenly however many there are — which is the whole point.
   */
  min?: number
  gap?: number
  style?: CSSProperties
  className?: string
}

/**
 * Phase 8 Track 5 — a KPI row that uses the whole width.
 *
 * The old pattern was `<Col xs={12} md={6}>` × 4: a fixed 4-up slice of a
 * 24-column grid. It looks right at exactly four cards and wrong at every
 * other count — three cards leave a quarter of the row empty on the right,
 * five wrap one lonely card onto its own line, and the Executive Summary's
 * `xl={4}` six-up leaves a third of the row blank whenever a KPI is hidden.
 * The dead space is always on the right, which reads as "something failed to
 * load" rather than "there are three of these".
 *
 * Flex, not a column count: every card takes `1 1 min` of the line, so N cards
 * are N equal shares of the FULL width, and below the breakpoint they wrap
 * into equal rows instead of a ragged grid. Nothing here needs to know how
 * many children it has, which is what stops the layout going stale when a KPI
 * is added or hidden by role.
 *
 * ⚠️ `height: 100%` on the card is not cosmetic. Flex items stretch to the
 * tallest in the line, so without it a two-line title leaves its neighbour
 * short and the row's baselines disagree. The rule lives in index.css beside
 * `.gi-kpi`.
 */
export default function KpiRow({ children, min = 220, gap = 16, style,
                                className }: Props) {
  // `Children.toArray` already drops null/undefined/booleans; the empty
  // string is the one falsy leaf it keeps, and it would otherwise take a full
  // share of the row as an invisible cell.
  const items = Children.toArray(children).filter((c) => c !== '')
  return (
    <div className={`gi-kpi-row${className ? ` ${className}` : ''}`}
      style={{ gap, ['--gi-kpi-min' as string]: `${min}px`, ...style }}>
      {items.map((c, i) => (
        <div className="gi-kpi-cell"
          key={isValidElement(c) && c.key != null ? c.key : i}>{c}</div>
      ))}
    </div>
  )
}
