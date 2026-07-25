import { useEffect, useRef } from 'react'
import { App } from 'antd'

/**
 * Phase 8-2 — global 429 handling. The axios interceptor fires
 * 'gi-rate-limited' with the server's Retry-After; this renders ONE sticky
 * warning toast that counts down live ("try again in 42 s…") and clears
 * itself. New 429s while counting just extend/replace the countdown.
 * Renders nothing — it only owns the toast.
 *
 * Also owns the 'gi-api-unreachable' toast: the axios interceptor fires it
 * (throttled to once per 30 s, and never while the device itself is offline)
 * when requests get no response or a 502/503/504 from the dev proxy — i.e.
 * the backend process is down, not the network.
 */
export default function RateLimitToast() {
  const { message } = App.useApp()
  const timer = useRef<number | null>(null)

  useEffect(() => {
    const KEY = 'gi-rate-limit'
    const stop = () => {
      if (timer.current != null) window.clearInterval(timer.current)
      timer.current = null
    }
    const onLimited = (e: Event) => {
      const seconds = Math.max(1, (e as CustomEvent<{ seconds: number }>).detail.seconds)
      // deadline-based (not tick-counted) so browser timer throttling in
      // background tabs can never freeze or skew the countdown
      const deadline = Date.now() + seconds * 1000
      stop()
      const tick = () => {
        const remaining = Math.ceil((deadline - Date.now()) / 1000)
        if (remaining <= 0) {
          stop()
          message.open({ key: KEY, type: 'success', duration: 3, content: 'You can try again now.' })
        } else {
          message.open({
            key: KEY, type: 'warning', duration: 0,
            content: `Too many requests — you can try again in ${remaining}s`,
          })
        }
      }
      tick()
      timer.current = window.setInterval(tick, 1000)
    }
    const onUnreachable = () => {
      message.open({
        key: 'gi-api-unreachable', type: 'error', duration: 8,
        content: import.meta.env.DEV
          ? 'API server unreachable — ensure the Python backend is running (uvicorn backend.api.main:app --port 8000).'
          : 'Server unreachable — please try again shortly or contact IT if it persists.',
      })
    }
    window.addEventListener('gi-rate-limited', onLimited)
    window.addEventListener('gi-api-unreachable', onUnreachable)
    return () => {
      window.removeEventListener('gi-rate-limited', onLimited)
      window.removeEventListener('gi-api-unreachable', onUnreachable)
      stop()
    }
  }, [message])

  return null
}
