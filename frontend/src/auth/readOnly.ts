/**
 * frontend/src/auth/readOnly.ts — the client half of the view-only (Auditor)
 * role. Mirrors backend/api/readonly.py; that file is the boundary, this one
 * is the courtesy that stops an auditor being shown buttons they cannot use.
 *
 * Three layers, weakest to strongest:
 *   1. `isReadOnly(user)`      — drives the UI (buttons hidden/disabled, a
 *                                "View only" tag in the header).
 *   2. the nav manifest        — pages that exist ONLY to change data are
 *                                marked `writes` and are not routable at all
 *                                for a read-only role (see config/nav.tsx).
 *   3. the axios interceptor   — a last-resort client-side stop, so a stray
 *                                mutating request never even leaves the tab.
 *
 * None of these is a security control. The server refuses the request whatever
 * the client does; layer 3 exists to turn a would-be 403 into an immediate,
 * legible message rather than a failed round trip.
 */
import type { User } from './AuthContext'

/** Roles that may read but never write. Keep in step with READ_ONLY_ROLES. */
export const READ_ONLY_ROLES = new Set(['auditor'])

export function isReadOnlyRole(role: string | undefined | null): boolean {
  return READ_ONLY_ROLES.has(String(role ?? ''))
}

export function isReadOnly(user: User | null): boolean {
  return !!user && isReadOnlyRole(user.role)
}

/** HTTP methods that cannot change server state. */
const SAFE_METHODS = new Set(['get', 'head', 'options', 'trace'])

/**
 * Paths that use a mutating verb but change nothing an auditor is barred from
 * — the exact mirror of `_ALLOWED_EXACT` / `_ALLOWED_PREFIXES` in
 * backend/api/readonly.py. If the two ever drift, the server wins and the user
 * gets a 403; keeping them in step only keeps the UX honest.
 */
const ALLOWED_EXACT = new Set([
  '/auth/login', '/auth/login/2fa', '/auth/refresh', '/auth/logout',
  '/auth/2fa/enroll', '/auth/2fa/verify', '/auth/2fa/disable',
  '/auth/phone/request-otp', '/auth/phone/verify-otp',
  '/sme/plan/cascade', '/sme/plan/export', '/sme/export/rows',
])

const ALLOWED_PREFIXES = [
  '/ai/assistant', '/ai/query', '/ai/nl-search', '/ai/insights', '/ai/eod-summary',
]

/** Strip the /api or /api/v1 mount and any trailing slash or query string. */
export function normalizePath(url: string): string {
  let p = String(url || '/').split('?')[0]
  // A native build's baseURL is absolute; reduce it to a pathname first.
  if (/^https?:\/\//i.test(p)) {
    try { p = new URL(p).pathname } catch { /* leave as-is */ }
  }
  p = p.replace(/^\/api\/v1/, '').replace(/^\/api/, '') || '/'
  if (p.length > 1) p = p.replace(/\/+$/, '') || '/'
  return p.startsWith('/') ? p : `/${p}`
}

export function isAllowedWrite(url: string): boolean {
  const p = normalizePath(url)
  return ALLOWED_EXACT.has(p) || ALLOWED_PREFIXES.some((a) => p.startsWith(a))
}

/** True when a read-only user's request must be stopped before it is sent. */
export function blocksRequest(role: string | null | undefined,
                              method: string, url: string): boolean {
  if (!isReadOnlyRole(role)) return false
  if (SAFE_METHODS.has(String(method || 'get').toLowerCase())) return false
  return !isAllowedWrite(url)
}
