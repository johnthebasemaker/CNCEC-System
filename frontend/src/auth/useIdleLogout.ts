/**
 * frontend/src/auth/useIdleLogout.ts — sign a user out after inactivity.
 *
 * Why this matters here specifically: the app runs on shared site terminals.
 * A Store Keeper who walks away from the entry desk previously left a session
 * good until someone closed the browser, and the refresh cookie is valid for 7
 * days (90 on the installed native apps) — so "I'll just lock the screen later"
 * was a genuinely open door.
 *
 * The design in three points:
 *
 * 1. **Real activity resets the clock, silently.** Pointer, keyboard, scroll,
 *    touch and wheel events all count, listened for in the capture phase so a
 *    component that stops propagation cannot accidentally starve the timer.
 *    They are throttled to one write per 5s — this fires on every mousemove
 *    otherwise, and the whole point is to be cheap.
 *
 * 2. **Two minutes of warning, with a way out.** Being dumped to a login
 *    screen mid-task with no notice is how people learn to distrust an app.
 *    The countdown says exactly how long is left and a single click stays
 *    signed in.
 *
 * 3. **Shared across tabs.** Last-activity is a localStorage timestamp, so
 *    working in one tab keeps every other tab alive. Without this, a second
 *    tab left open on a dashboard would sign the user out from under the tab
 *    they were actually using.
 *
 * The client is not the security boundary — it cannot be. What makes this real
 * is that the logout it triggers is the ordinary one, which POSTs /auth/logout
 * and REVOKES the refresh-token family server-side. After an idle logout the
 * session is genuinely dead, not merely hidden: the cookie left in the browser
 * can no longer mint an access token.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

/** Total idle time before sign-out. */
export const IDLE_TIMEOUT_MS = 30 * 60 * 1000
/** How long the warning is visible before the timeout fires. */
export const IDLE_WARNING_MS = 2 * 60 * 1000
/** Shared across tabs; also survives a reload of a single tab. */
const LAST_ACTIVITY_KEY = 'gi_last_activity'
/** Don't write to localStorage more than once per this interval. */
const WRITE_THROTTLE_MS = 5000
/** How often we compare now() against the stored timestamp. */
const POLL_MS = 1000

const ACTIVITY_EVENTS = [
  'pointerdown', 'keydown', 'wheel', 'touchstart', 'scroll',
] as const

function readLastActivity(): number {
  try {
    const v = Number(localStorage.getItem(LAST_ACTIVITY_KEY))
    return Number.isFinite(v) && v > 0 ? v : Date.now()
  } catch {
    return Date.now()
  }
}

function writeLastActivity(ts: number) {
  try { localStorage.setItem(LAST_ACTIVITY_KEY, String(ts)) } catch { /* private mode */ }
}

/** Pure helper — exported so the behaviour is testable without fake timers. */
export function idleState(lastActivity: number, now: number): 'active' | 'warning' | 'expired' {
  const idle = now - lastActivity
  if (idle >= IDLE_TIMEOUT_MS) return 'expired'
  if (idle >= IDLE_TIMEOUT_MS - IDLE_WARNING_MS) return 'warning'
  return 'active'
}

export interface IdleLogout {
  /** True while the countdown modal should be shown. */
  warning: boolean
  /** Whole seconds left before sign-out (0 when not warning). */
  secondsLeft: number
  /** "Stay signed in" — resets the clock. */
  staySignedIn: () => void
}

/**
 * @param enabled  false while signed out — no timers, no listeners.
 * @param onIdle   called once when the idle limit is reached.
 */
export function useIdleLogout(enabled: boolean, onIdle: () => void): IdleLogout {
  const [warning, setWarning] = useState(false)
  const [secondsLeft, setSecondsLeft] = useState(0)
  const lastWrite = useRef(0)
  const firedRef = useRef(false)
  // Keep the callback in a ref so re-renders don't tear down the listeners.
  const onIdleRef = useRef(onIdle)
  onIdleRef.current = onIdle

  const touch = useCallback(() => {
    const now = Date.now()
    if (now - lastWrite.current < WRITE_THROTTLE_MS) return
    lastWrite.current = now
    writeLastActivity(now)
  }, [])

  const staySignedIn = useCallback(() => {
    const now = Date.now()
    lastWrite.current = now
    writeLastActivity(now)
    setWarning(false)
    setSecondsLeft(0)
  }, [])

  useEffect(() => {
    if (!enabled) {
      setWarning(false)
      setSecondsLeft(0)
      firedRef.current = false
      return
    }
    // Signing in counts as activity — otherwise a stale timestamp from a
    // previous session could expire the new one within seconds.
    const start = Date.now()
    lastWrite.current = start
    writeLastActivity(start)
    firedRef.current = false

    const onActivity = () => touch()
    for (const ev of ACTIVITY_EVENTS) {
      window.addEventListener(ev, onActivity, { capture: true, passive: true })
    }

    const tick = () => {
      if (firedRef.current) return
      const last = readLastActivity()
      const state = idleState(last, Date.now())
      if (state === 'expired') {
        firedRef.current = true
        setWarning(false)
        onIdleRef.current()
        return
      }
      if (state === 'warning') {
        setWarning(true)
        setSecondsLeft(Math.max(0, Math.ceil((last + IDLE_TIMEOUT_MS - Date.now()) / 1000)))
      } else {
        setWarning(false)
        setSecondsLeft(0)
      }
    }

    // One 1 Hz timer, always. A second interval would let the countdown lie by
    // up to POLL_MS while it matters most, and reading one localStorage key per
    // second costs nothing next to that.
    const id = window.setInterval(tick, POLL_MS)
    // A laptop that slept through the timeout must not come back "active":
    // intervals do not fire while suspended, so re-check the moment the tab
    // becomes visible again.
    const onVisible = () => { if (document.visibilityState === 'visible') tick() }
    document.addEventListener('visibilitychange', onVisible)
    tick()

    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
      for (const ev of ACTIVITY_EVENTS) {
        window.removeEventListener(ev, onActivity, { capture: true })
      }
    }
  }, [enabled, touch])

  return { warning, secondsLeft, staySignedIn }
}
