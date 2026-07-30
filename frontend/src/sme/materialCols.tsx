/**
 * frontend/src/sme/materialCols.tsx — how a material COMPONENT is rendered.
 *
 * 2026-07-30 COMPONENT IDENTITY: a multi-part chemical system is several
 * physical drums sharing one Material_Code, separated only by the variant SAP
 * (GI-8005766 → Comp-A/B/C/D at 1042 / 1042-1 / 1042-2 / 1042-3). Two rules
 * follow, and every SME grid uses these helpers so they hold everywhere:
 *
 *   • the SAP rides under the code, because four identical-looking codes in a
 *     column are unreadable — the SAP is the only thing that says which drum;
 *   • the name WRAPS instead of ellipsing. The names that matter most here are
 *     the longest ones ("CUMICRETE PU MF 300 (1MM) C"), and truncating them to
 *     "CUMICRETE PU MF 300 (1MM…" throws away the one character that
 *     distinguishes the component from its three siblings.
 */
import type { ColumnType } from 'antd/es/table'

const mono: React.CSSProperties = { fontFamily: 'JetBrains Mono, monospace' }

interface HasComponent {
  Material_Code?: string | null
  SAP_Code?: string | null
  Material_Name?: string | null
}

/** Material_Code with its variant SAP underneath. */
export function materialCodeCol<T extends HasComponent>(
  opts: { title?: string; width?: number; fixed?: 'left' } = {},
): ColumnType<T> {
  return {
    title: opts.title ?? 'Material',
    dataIndex: 'Material_Code',
    key: 'Material_Code',
    width: opts.width ?? 150,
    ...(opts.fixed ? { fixed: opts.fixed } : {}),
    render: (v: string, r: T) => (
      <div style={{ lineHeight: 1.25 }}>
        <span style={mono}>{v || '—'}</span>
        {r.SAP_Code ? (
          <div style={{ ...mono, fontSize: 11, opacity: 0.6 }}>SAP {r.SAP_Code}</div>
        ) : null}
      </div>
    ),
  }
}

/** Full component name, wrapped — never ellipsed. */
export function materialNameCol<T extends HasComponent>(
  opts: { title?: string; width?: number } = {},
): ColumnType<T> {
  return {
    title: opts.title ?? 'Material Name',
    dataIndex: 'Material_Name',
    key: 'Material_Name',
    width: opts.width ?? 240,
    render: (v: string) => (
      <span style={{ whiteSpace: 'normal', wordBreak: 'break-word', lineHeight: 1.3 }}>
        {v || '—'}
      </span>
    ),
  }
}
