import axios from 'axios'

// API base URL. Web builds leave VITE_API_URL unset and use the relative
// '/api' prefix (Vite dev proxy → uvicorn :8000; nginx in production). The
// NATIVE builds (Tauri/Capacitor, see .github/workflows/release-*.yml) inject
// VITE_API_URL=https://gi.giinventory.com/api so the standalone binaries talk
// to the hosted backend — their origin is tauri://localhost etc., so relative
// paths would otherwise resolve nowhere.
export const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/+$/, '') || '/api'

// withCredentials keeps the httpOnly refresh cookie flowing when API_BASE is
// cross-origin (native apps); it is a no-op for same-origin web requests.
export const api = axios.create({ baseURL: API_BASE, withCredentials: true })

// RTR client identity: the backend issues a 90-day refresh-token family to
// installed native apps ('native') and a 7-day one to browsers ('web').
// Tauri v2 injects __TAURI_INTERNALS__ into its webview; Capacitor exposes
// window.Capacitor and isNativePlatform() is false when the same bundle runs
// in a plain browser. Sent with the login payload only — the server pins the
// choice inside the signed token family afterwards.
export function detectClientType(): 'web' | 'native' {
  const w = window as unknown as Record<string, unknown>
  if ('__TAURI_INTERNALS__' in w || '__TAURI__' in w) return 'native'
  const cap = w.Capacitor as { isNativePlatform?: () => boolean } | undefined
  if (cap?.isNativePlatform?.()) return 'native'
  return 'web'
}

// --- auth token plumbing -----------------------------------------------------
export const TOKEN_KEY = 'gi_token'
let _token: string | null = localStorage.getItem(TOKEN_KEY)

export function setAuthToken(token: string | null) {
  _token = token
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

// For fetch-based callers (SSE streams) that can't use the axios interceptor.
export function getAuthToken(): string | null {
  return _token
}

// --- WhatsApp delivery preference (Phase 6, refined post-UAT) ------------------
// "urgent" (default) = WhatsApp alerts send immediately; "evening" = staged and
// batched into one 16:00 digest. The X-Delivery-Preference header is sent ONLY
// by the material-transaction mutations (issue / receive / return) — see
// deliveryHeaders() below. Profile changes and OTP requests never carry it, so
// they always bypass the digest queue. localStorage just keeps the form
// selector sticky across the three entry pages.
export const DELIVERY_PREF_KEY = 'gi-delivery-pref'

export function getDeliveryPreference(): 'urgent' | 'evening' {
  return localStorage.getItem(DELIVERY_PREF_KEY) === 'evening' ? 'evening' : 'urgent'
}

export function setDeliveryPreference(pref: 'urgent' | 'evening') {
  if (pref === 'evening') localStorage.setItem(DELIVERY_PREF_KEY, pref)
  else localStorage.removeItem(DELIVERY_PREF_KEY)
}

// Headers for a transaction POST: {} when urgent (backend default) so the
// header only appears when the user explicitly chose the evening digest.
export function deliveryHeaders(): Record<string, string> {
  const pref = getDeliveryPreference()
  return pref === 'evening' ? { 'X-Delivery-Preference': pref } : {}
}

api.interceptors.request.use((cfg) => {
  if (_token) cfg.headers.Authorization = `Bearer ${_token}`
  return cfg
})

// --- silent session refresh ---------------------------------------------------
// Access tokens are short-lived (15 min); the long-lived rotating refresh token
// lives in an httpOnly cookie the JS never sees. On any 401 we try ONE silent
// refresh (single-flight across concurrent 401s) and replay the request — a
// worker mid-shift never notices. Only when the refresh itself fails is the
// session truly over.
const NO_RETRY = ['/auth/login', '/auth/login/2fa', '/auth/refresh', '/auth/register']

let _refreshing: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  try {
    // Raw axios, not `api` — the interceptor below must not recurse.
    // RTR: this response also carries the ROTATED refresh token, but as an
    // httpOnly Set-Cookie the JS never sees — the browser/webview stores the
    // new cookie automatically (withCredentials covers the cross-origin
    // native case). Only the short-lived access token lives in JS.
    const { data } = await axios.post(`${API_BASE}/auth/refresh`, null, {
      withCredentials: true,
    })
    const t = (data?.access_token as string) ?? null
    if (t) setAuthToken(t)
    return t
  } catch {
    return null
  }
}

// --- unreachable-backend detection --------------------------------------------
// A request with NO response (network error) or a 502/503/504 from the dev
// proxy means the API itself is down, not that the call was wrong. Log a
// clear, throttled hint (the classic dev trap is Vite up + uvicorn down →
// every call 502s) and raise a window event so AppLayout can toast it.
let _lastUnreachableLog = 0

function noteApiUnreachable(url: string, status?: number) {
  // Genuinely offline → the offline queue/badge owns that state; a "backend
  // down" hint would be wrong.
  if (!navigator.onLine) return
  const now = Date.now()
  if (now - _lastUnreachableLog < 30_000) return
  _lastUnreachableLog = now
  const where = status ? `${status} from the API proxy` : 'network error'
  console.error(
    `[GI Hub] API unreachable (${where}, ${API_BASE}${url}). ` +
      'Ensure the Python backend is running: ' +
      '.venv/bin/uvicorn backend.api.main:app --host 127.0.0.1 --port 8000',
  )
  window.dispatchEvent(new CustomEvent('gi-api-unreachable', { detail: { url, status } }))
}

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const cfg = err?.config
    const url: string = cfg?.url ?? ''
    const status: number | undefined = err?.response?.status
    if (!err?.response || status === 502 || status === 503 || status === 504) {
      noteApiUnreachable(url, status)
    }
    // Phase 8-2 — rate limited: surface a global countdown toast (handled in
    // AppLayout) with the server's Retry-After. The error still rejects so
    // each caller's own handler runs too.
    if (err?.response?.status === 429) {
      const retryAfter = Number(err.response.headers?.['retry-after']) || 30
      window.dispatchEvent(new CustomEvent('gi-rate-limited', {
        detail: { seconds: retryAfter, url },
      }))
    }
    if (
      err?.response?.status === 401 &&
      cfg &&
      !cfg._retried &&
      !NO_RETRY.some((p) => url.startsWith(p))
    ) {
      cfg._retried = true
      _refreshing ??= refreshAccessToken().finally(() => {
        _refreshing = null
      })
      const t = await _refreshing
      // The request interceptor re-stamps Authorization from the new token.
      if (t) return api(cfg)
      if (_token) {
        setAuthToken(null)
        window.dispatchEvent(new Event('gi-session-expired'))
      }
    }
    return Promise.reject(err)
  },
)

export type Row = Record<string, unknown>

export interface ListResponse<T = Row> {
  total: number
  limit: number
  offset: number
  count: number
  items: T[]
}

export interface InventorySummary {
  total_items: number
  by_site: { Site_ID: string | null; count: number }[]
  by_category: { Category: string | null; count: number }[]
}

export interface Health {
  status: string
  dialect: string
  database: string
  entities: string[]
}

export async function fetchList<T = Row>(
  path: string,
  params: Record<string, unknown> = {},
): Promise<ListResponse<T>> {
  const { data } = await api.get<ListResponse<T>>(path, { params })
  return data
}
