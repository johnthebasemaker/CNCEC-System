/**
 * frontend/src/lib/ppe.ts — the pure helpers behind the PPE issue panel.
 *
 * Split out of PpeIssueFields.tsx so that file exports a component and
 * nothing else (fast refresh stops working on a module that mixes the two),
 * and so these three can be reasoned about — and reused — without importing
 * antd.
 */
import type { Row } from '../api/client'
import type { EntryDoc } from '../components/EntryDocsUpload'

/** What the issue form collects for a PPE line, per LINE not per batch. */
export interface PpeState {
  employeeId: string
  doc: EntryDoc[]
  earlyReason: string
}

export const emptyPpe = (): PpeState => ({ employeeId: '', doc: [], earlyReason: '' })

/** The usable-time rule for one material, as /ppe/eligible reports it. */
export interface PpeRule {
  SAP_Code: string
  usable_days: number | null
  requires_safety_doc: boolean
}

/**
 * The rule for the picked material, or null when it is not PPE at all.
 *
 * Null is the common case — ~450 of the 466 materials in the master are not
 * PPE — and it is what keeps the issue form looking exactly as it did for
 * them. `usable_days: null` is a DIFFERENT state: the item IS PPE but nobody
 * has said how long it lasts, so it can be issued and recorded without a
 * replacement date.
 */
export function findPpeRule(
  eligible: Row[] | undefined, sap: string | undefined,
): PpeRule | null {
  if (!sap) return null
  const hit = (eligible ?? []).find((r) => String(r.SAP_Code) === String(sap))
  if (!hit) return null
  return {
    SAP_Code: String(hit.SAP_Code),
    usable_days: hit.usable_days == null ? null : Number(hit.usable_days),
    requires_safety_doc: hit.requires_safety_doc !== false,
  }
}

/**
 * The person's current issue of THIS material, if they have one.
 *
 * Reads the `active` array from /ppe/employees/{id}, which is the PERSON's
 * across every site — so a worker who transferred in last week still shows
 * the boots issued at their old site. That is the whole point of ruling R1.
 */
export function activeHolding(history: Row | undefined, sap: string | undefined) {
  if (!history || !sap) return null
  const active = (history.active as Row[] | undefined) ?? []
  return active.find((h) => String(h.SAP_Code) === String(sap)) ?? null
}
