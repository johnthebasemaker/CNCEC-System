/**
 * frontend/src/api/idempotency.ts — one key per intent, not per click.
 *
 * The four dangerous procurement actions (create a PR, submit it, raise a PO,
 * assign one) accept an `Idempotency-Key` header. The server claims the key
 * before doing the work and replays the stored answer on a repeat, so the
 * SECOND of two identical requests is not a second purchase order.
 *
 * ⚠️ THE KEY MUST SURVIVE THE RETRY AND NOT THE NEXT REQUEST. That is the whole
 * design problem here, and both halves matter:
 *
 *   · a key generated per CLICK protects nothing — a double-click sends two
 *     different keys and the server sees two different orders;
 *   · a key that never changes protects too much — the second, genuinely
 *     different PR replays the first one's answer and is never created.
 *
 * So a key is minted per FORM MOUNT and retired the moment its action
 * succeeds. Between those two points every retry — double-click, dropped
 * connection, the user pressing the button again because nothing happened —
 * carries the same key and resolves to one order.
 *
 * `crypto.randomUUID` is available in every browser this app supports and in
 * the Playwright runtime; the fallback exists for insecure origins (plain
 * http on a LAN), where it is absent.
 */
import { useCallback, useRef } from 'react'

function newKey(): string {
  const c = globalThis.crypto as Crypto | undefined
  if (c?.randomUUID) return c.randomUUID()
  // Not cryptographic, and it does not need to be: the key only has to be
  // unique among this user's own in-flight requests, and the server scopes it
  // by user and action on top of that.
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

export interface IdempotencyKey {
  /** The current key — send it as the `Idempotency-Key` header. */
  current: () => string
  /** Retire the key after a SUCCESS, so the next distinct action gets its own. */
  rotate: () => void
}

/**
 * A key that lives from mount until the action it guards succeeds.
 *
 * ⚠️ `useRef`, not `useState`: reading the key must not depend on a re-render
 * having happened, and rotating it must not schedule one. A `setState` here
 * would mean the click handler could read the PREVIOUS key — which is exactly
 * the failure this hook exists to prevent.
 */
export function useIdempotencyKey(): IdempotencyKey {
  const ref = useRef<string>('')
  if (!ref.current) ref.current = newKey()
  const current = useCallback(() => {
    if (!ref.current) ref.current = newKey()
    return ref.current
  }, [])
  const rotate = useCallback(() => { ref.current = newKey() }, [])
  return { current, rotate }
}

/** Header object for an axios/fetch call. */
export function idemHeaders(key: string): Record<string, string> {
  return key ? { 'Idempotency-Key': key } : {}
}
