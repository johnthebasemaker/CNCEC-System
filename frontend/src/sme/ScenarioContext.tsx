/**
 * frontend/src/sme/ScenarioContext.tsx — persistent SME planning scenario
 * (Phase S1). Holds the equipment priority order that drives the client-side
 * cascade engine. State is React-only (the backend stays read-only per the
 * SME Canon) and persists to localStorage, so a planning session survives a
 * refresh — something the Streamlit portal never could.
 *
 * ⚠️ SCOPED BY USER *AND* SITE (v2). v1 keyed this store by site alone, so
 * signing out of an admin account and back in as an HOD at that site handed
 * the HOD the admin's selected equipment, still sitting in the tab. The key is
 * now `username::siteKey`. Two things follow, and both are deliberate:
 *   · the same person returning still finds their planning work — scoping is
 *     not the same as wiping;
 *   · a DIFFERENT person can never address the previous one's entry, whatever
 *     happens to the storage clear in auth/sessionState.ts.
 * The v1 key is not migrated: it is unattributable (nothing records who wrote
 * it), and inheriting it is precisely the bug. sessionState.ts deletes it.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useAuth } from '../auth/AuthContext'

const STORAGE_KEY = 'gi.sme.scenario.v2'

type Store = Record<string, string[]> // "username::siteKey" → ordered tags

function readStore(): Store {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? (parsed as Store) : {}
  } catch {
    return {} // corrupted storage → start clean, never crash the portal
  }
}

function writeStore(store: Store) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store))
  } catch {
    /* quota/private-mode failures are non-fatal: scenario stays in memory */
  }
}

// --- URL sharing (Phase S3) ---------------------------------------------------
// The priority order is also encoded into ?scenario= so a planning session can
// be shared as a link. Equipment tags never contain '~' (SAP-style codes), so
// '~' delimits encodeURIComponent()-escaped tags. URL wins over localStorage
// on first load (an opened share-link shows the sender's exact scenario).
const URL_PARAM = 'scenario'

function readUrlOrder(): string[] | null {
  const p = new URLSearchParams(window.location.search).get(URL_PARAM)
  if (!p) return null
  const tags = p.split('~').map((t) => {
    try { return decodeURIComponent(t).trim() } catch { return '' }
  }).filter(Boolean)
  return tags.length ? [...new Set(tags)] : null
}

function writeUrlOrder(order: string[]) {
  try {
    const url = new URL(window.location.href)
    if (order.length) url.searchParams.set(URL_PARAM, order.map(encodeURIComponent).join('~'))
    else url.searchParams.delete(URL_PARAM)
    window.history.replaceState(null, '', url)
  } catch {
    /* non-fatal: sharing degrades, scenario still works */
  }
}

export interface ScenarioState {
  /** Site this scenario belongs to ('all' for the admin cross-site view). */
  siteKey: string
  /** Equipment tags in priority order (top = allocated first). */
  order: string[]
  setOrder: (next: string[]) => void
  addTag: (tag: string) => void
  /**
   * Append many tags in ONE update, preserving the given order and skipping
   * any already in the session. Returns nothing; read `order` for the result.
   * Bulk add exists as its own action because looping `addTag` would write
   * localStorage once per tag and re-cascade the whole plan on every step —
   * with "Select all" over 29 equipment that is 29 full engine runs.
   */
  addTags: (tags: string[]) => void
  removeTag: (tag: string) => void
  /** Move the tag at `from` to position `to` (dnd-kit reorder handler). */
  moveTag: (from: number, to: number) => void
  clear: () => void
  /** Current shareable URL (already synced on every change). */
  shareUrl: () => string
}

const ScenarioContext = createContext<ScenarioState | null>(null)

export function ScenarioProvider({ siteId, children }: { siteId?: string; children: ReactNode }) {
  const { user } = useAuth()
  const siteKey = siteId ?? 'all'
  // The storage address. Signed out (never rendered in practice — SmePage sits
  // behind the route guard) falls back to 'anon', which simply means nothing
  // reachable by a real account.
  const storeKey = `${user?.username ?? 'anon'}::${siteKey}`
  const [order, setOrderState] = useState<string[]>(
    () => readUrlOrder() ?? readStore()[storeKey] ?? [])

  // First mount: a ?scenario= URL wins (and is persisted so refresh keeps it).
  // A site switch — or a user switch — afterwards loads that scope's scenario.
  const firstMount = useRef(true)
  useEffect(() => {
    const fromUrl = firstMount.current ? readUrlOrder() : null
    firstMount.current = false
    const next = fromUrl ?? readStore()[storeKey] ?? []
    setOrderState(next)
    if (fromUrl) writeStore({ ...readStore(), [storeKey]: fromUrl })
    writeUrlOrder(next)
  }, [storeKey])

  const persist = useCallback((next: string[]) => {
    writeStore({ ...readStore(), [storeKey]: next })
    writeUrlOrder(next)
  }, [storeKey])

  const setOrder = useCallback((next: string[]) => {
    const clean = [...new Set(next.map((t) => t.trim()).filter(Boolean))]
    setOrderState(clean)
    persist(clean)
  }, [persist])

  const addTag = useCallback((tag: string) => {
    setOrderState((prev) => {
      const t = tag.trim()
      if (!t || prev.includes(t)) return prev
      const next = [...prev, t]
      persist(next)
      return next
    })
  }, [persist])

  const addTags = useCallback((tags: string[]) => {
    setOrderState((prev) => {
      const have = new Set(prev)
      const fresh: string[] = []
      for (const raw of tags) {
        const t = raw.trim()
        if (!t || have.has(t)) continue
        have.add(t)      // also de-dupes repeats WITHIN the incoming list
        fresh.push(t)
      }
      if (!fresh.length) return prev   // nothing new — do not touch storage
      const next = [...prev, ...fresh]
      persist(next)
      return next
    })
  }, [persist])

  const removeTag = useCallback((tag: string) => {
    setOrderState((prev) => {
      const next = prev.filter((t) => t !== tag)
      persist(next)
      return next
    })
  }, [persist])

  const moveTag = useCallback((from: number, to: number) => {
    setOrderState((prev) => {
      if (from === to || from < 0 || to < 0 || from >= prev.length || to >= prev.length) return prev
      const next = [...prev]
      const [item] = next.splice(from, 1)
      next.splice(to, 0, item)
      persist(next)
      return next
    })
  }, [persist])

  const clear = useCallback(() => setOrder([]), [setOrder])
  const shareUrl = useCallback(() => window.location.href, [])

  const value = useMemo(
    () => ({ siteKey, order, setOrder, addTag, addTags, removeTag, moveTag, clear, shareUrl }),
    [siteKey, order, setOrder, addTag, addTags, removeTag, moveTag, clear, shareUrl],
  )
  return <ScenarioContext.Provider value={value}>{children}</ScenarioContext.Provider>
}

export function useScenario(): ScenarioState {
  const ctx = useContext(ScenarioContext)
  if (!ctx) throw new Error('useScenario must be used inside <ScenarioProvider>')
  return ctx
}
