/**
 * frontend/src/sme/SystemCode.tsx — how a lining system code is PRINTED.
 *
 * One component, because the alternative is fourteen copies of `{r.code}` that
 * drift the moment one of them learns something the others do not.
 *
 * ⚠️ CV/ME IS A PROPERTY OF THE (TAG, CODE) ROW, NOT OF THE CODE. In the live
 * master `LSC1` is CV on nine concrete rows and ME on nineteen tank/vessel
 * rows. So there are two honest ways to print a code and the caller has to say
 * which it means:
 *
 *   · per-equipment context (a row that IS one tag+code) → pass that row's
 *     `type`. Exact, unambiguous: `LSC1 [ME]`.
 *   · aggregate context (a filter, a code rollup across equipment) → pass the
 *     SET via `useCodeTypes`. Prints `LSC1 [CV/ME]`.
 *
 * What it must never do is pick whichever Type it met first and present that as
 * the code's discipline. That is an invented aggregate: it reads as fact, it is
 * wrong half the time, and nothing on screen admits either.
 *
 * The backend does exactly the same thing in `services/jobs.py` (`code_chip`)
 * for the labels IT assembles. These two are not a parity pair — the backend
 * owns every label that ships in an export or an API payload, and this file
 * only decorates rows the client already holds.
 */
import { useMemo } from 'react'
import { Tag, Tooltip } from 'antd'

/** `LSC4 [CV]`, or `LSC1 [CV/ME]` where the code spans both disciplines. */
export function codeChip(code: string, types?: string | string[] | Set<string>): string {
  const c = (code ?? '').trim()
  if (!c) return 'Surface prep'
  let parts: string[] = []
  if (typeof types === 'string') parts = types.split('/')
  else if (Array.isArray(types)) parts = types
  else if (types) parts = [...types]
  const clean = [...new Set(parts.map((p) => p.trim().toUpperCase()).filter(Boolean))].sort()
  return clean.length ? `${c} [${clean.join('/')}]` : c
}

/** code → the disciplines it is used in, across the units given. */
export function useCodeTypes(units: { code: string; type?: string }[] | undefined) {
  return useMemo(() => {
    const m = new Map<string, Set<string>>()
    for (const u of units ?? []) {
      const c = (u.code ?? '').trim()
      if (!c) continue
      if (!m.has(c)) m.set(c, new Set())
      const t = (u.type ?? '').trim().toUpperCase()
      if (t) m.get(c)!.add(t)
    }
    return m
  }, [units])
}

interface Props {
  code: string
  /** This row's exact discipline, OR the set for an aggregate view. */
  type?: string | string[] | Set<string>
  /** Full system name, e.g. "Carbon Brick Lining 30mm". Shown as a tooltip. */
  name?: string
  /** Render as a plain span instead of an antd Tag (for dense tables). */
  plain?: boolean
}

export default function SystemCode({ code, type, name, plain }: Props) {
  const label = codeChip(code, type)
  const body = plain ? (
    <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '0.82rem' }}>
      {label}
    </span>
  ) : (
    <Tag style={{ marginInlineEnd: 0 }}>{label}</Tag>
  )
  // The name is a tooltip rather than inline text: it is 30-40 characters
  // ("Polyurethane Resin Acid Resistant 5mm") and would wreck every column it
  // was pasted into. It is on the row header where there is room.
  return name ? <Tooltip title={name}>{body}</Tooltip> : body
}
