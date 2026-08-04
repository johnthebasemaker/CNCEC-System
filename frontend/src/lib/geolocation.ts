/**
 * frontend/src/lib/geolocation.ts — a position, when one is available.
 *
 * Native `navigator.geolocation` and a Google Maps LINK. No Leaflet, no tile
 * host, no map library:
 *
 *  · the capture is the part with operational value — a lat/lng stored on the
 *    movement row — and that needs no library at all;
 *  · a display map costs ~150 KB plus an external tile host on every page
 *    load, in a PWA that currently precaches 82 entries and is expected to
 *    work offline in a warehouse;
 *  · an anchor to `maps.google.com` opens the device's real map app, which is
 *    what someone standing in a yard actually wants.
 *
 * If an embedded map is ever wanted, it drops in behind the same stored
 * columns with no schema change. Nothing here forecloses it.
 *
 * ⚠️ THREE THINGS THIS MUST SURVIVE, all of them normal rather than
 * exceptional:
 *
 *  1. NO SECURE CONTEXT. `geolocation` is unavailable outside HTTPS and
 *     localhost. Production and dev both qualify; a bare-IP LAN build does
 *     NOT, and would otherwise fail silently — so it is detected and named.
 *  2. PERMISSION DENIED. Per-user, revocable, and frequently refused.
 *  3. NO FIX. Indoors, in a steel warehouse, a device can hold a permission
 *     and still never resolve a position.
 *
 * In all three the caller gets `{ ok: false, reason }` and MUST carry on. The
 * location update is the thing with value; the coordinate is the bonus. This
 * module never throws.
 */

export interface GpsFix {
  lat: number
  lng: number
  accuracy_m?: number
}

export type GpsResult =
  | { ok: true; fix: GpsFix }
  | { ok: false; reason: string }

/** Milliseconds before we stop waiting for a fix. Long enough for a cold GPS
 *  start outdoors, short enough that a warehouse with no signal does not make
 *  the button feel broken. */
const TIMEOUT_MS = 8000

export function geolocationAvailable(): boolean {
  return typeof navigator !== 'undefined'
    && 'geolocation' in navigator
    && (typeof window === 'undefined' || window.isSecureContext !== false)
}

/** Why capture is unavailable, in words a user can act on — or null when it is
 *  available. Used to explain a disabled control instead of just dimming it. */
export function geolocationBlockedReason(): string | null {
  if (typeof navigator === 'undefined' || !('geolocation' in navigator)) {
    return 'This browser has no location support.'
  }
  if (typeof window !== 'undefined' && window.isSecureContext === false) {
    return 'Location needs a secure page (https, or localhost). '
      + 'Open the app over HTTPS to capture coordinates.'
  }
  return null
}

/**
 * Ask for the current position. Resolves — never rejects.
 *
 * `enableHighAccuracy` is on because the useful question is "which yard, which
 * bay", not "which city", and the extra second is worth it for a scan the user
 * is standing still for.
 */
export function getPosition(): Promise<GpsResult> {
  const blocked = geolocationBlockedReason()
  if (blocked) return Promise.resolve({ ok: false, reason: blocked })

  return new Promise<GpsResult>((resolve) => {
    let settled = false
    const done = (r: GpsResult) => { if (!settled) { settled = true; resolve(r) } }
    try {
      navigator.geolocation.getCurrentPosition(
        (pos) => done({
          ok: true,
          fix: {
            lat: pos.coords.latitude,
            lng: pos.coords.longitude,
            accuracy_m: Number.isFinite(pos.coords.accuracy)
              ? pos.coords.accuracy : undefined,
          },
        }),
        (err) => done({
          ok: false,
          reason: err.code === err.PERMISSION_DENIED
            ? 'Location permission was declined — the move was still recorded.'
            : err.code === err.POSITION_UNAVAILABLE
              ? 'No position available here (common indoors) — the move was still recorded.'
              : 'Location timed out — the move was still recorded.',
        }),
        { enableHighAccuracy: true, timeout: TIMEOUT_MS, maximumAge: 0 },
      )
    } catch {
      done({ ok: false, reason: 'Location could not be read on this device.' })
    }
  })
}

/** Where to send someone who wants to see the point on a map. */
export function mapsUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps?q=${lat},${lng}`
}

/** "24.712300, 46.675300 ±12 m" — coordinates a person can read back. */
export function formatFix(lat?: number | null, lng?: number | null,
                          accuracy?: number | null): string {
  if (lat == null || lng == null) return '—'
  const acc = accuracy != null && Number.isFinite(accuracy)
    ? ` ±${Math.round(accuracy)} m` : ''
  return `${lat.toFixed(6)}, ${lng.toFixed(6)}${acc}`
}
