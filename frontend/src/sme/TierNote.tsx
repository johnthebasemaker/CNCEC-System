/**
 * frontend/src/sme/TierNote.tsx — the one-line legend that makes the
 * 2026-08-03 STRICT TIER SEGREGATION visible on every SME tab.
 *
 * The rule it explains: "Ready to build" is a claim about drums on a shelf.
 * Stock on a purchase order is not on a shelf, so it never counts toward
 * readiness — only toward the forward-looking "with ordered" twin of each
 * figure. Green = available now. Amber = on order. Red = still to buy.
 *
 * This exists because the numbers alone could not carry the distinction: an
 * operator reading a single "Coverage 100%" pill had no way to tell whether
 * the material was on the shelf or on a truck. Every tab that shows a
 * readiness figure renders this.
 */
import { Alert } from 'antd'

const dot = (color: string): React.CSSProperties => ({
  display: 'inline-block', width: 8, height: 8, borderRadius: 4,
  background: color, marginRight: 5, verticalAlign: 'middle',
})

export default function TierNote({ style }: { style?: React.CSSProperties }) {
  return (
    <Alert
      type="info"
      showIcon
      style={{ fontSize: '0.75rem', ...style }}
      message="Available stock and stock on order are counted separately"
      description={(
        <span>
          <b><span style={dot('#10B981')} />Available / “now”</b> is what is on the
          shelf today — the only thing that can make a unit “Ready to Build”.{' '}
          <b><span style={dot('#F59E0B')} />On order / “with ordered”</b> is stock on an
          open purchase order: a forecast of what becomes buildable when it lands,
          never a readiness figure.{' '}
          <b><span style={dot('#EF4444')} />To buy</b> is the gap that remains after
          on-order stock — the quantity to raise a PR against.
        </span>
      )}
    />
  )
}
