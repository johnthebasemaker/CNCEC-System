/**
 * frontend/src/auth/useReadOnly.ts — the hook pages use to disable actions.
 *
 * Most write UI never renders for a view-only account, because the pages that
 * exist to change data are gated out of the nav manifest entirely (`writes` in
 * config/nav.tsx). This hook is for the remainder: a control that lives on a
 * page an auditor legitimately reads — the upload button on Documents, "Send
 * to WhatsApp" on Reports.
 *
 *   const ro = useReadOnly()
 *   <Button {...ro.guard()} onClick={save}>Save</Button>
 *
 * `guard()` disables the control and explains why on hover, which is kinder
 * than hiding it: the auditor can see the feature exists and that their role
 * is the reason it is unavailable, rather than wondering if the page is broken.
 * Use `hidden` for controls where even the affordance would be noise.
 */
import { useAuth } from './AuthContext'

export const READ_ONLY_REASON =
  'Your account is view-only (Auditor) — this action changes data.'

export interface ReadOnlyGuard {
  /** True when the signed-in account may not change data. */
  readOnly: boolean
  /** Spread onto an antd Button/Upload/Switch to disable + explain it. */
  guard: () => { disabled?: boolean; title?: string }
  /** True when a control should not render at all. */
  hidden: boolean
}

export function useReadOnly(): ReadOnlyGuard {
  const { readOnly } = useAuth()
  return {
    readOnly,
    guard: () => (readOnly ? { disabled: true, title: READ_ONLY_REASON } : {}),
    hidden: readOnly,
  }
}
