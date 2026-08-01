// Barcode/QR material picking (UAT Phase 3 QoL). Decoding happens client-side
// in QrScanner (BarcodeDetector when available, jsQR fallback for QR); this
// module maps a decoded string onto an inventory SAP code.

import type { Row } from '../api/client'

// 1-D retail/logistics formats + QR — what BarcodeDetector supports broadly.
export const BARCODE_FORMATS = [
  'qr_code', 'code_128', 'code_39', 'code_93', 'ean_13', 'ean_8',
  'upc_a', 'upc_e', 'itf', 'codabar', 'data_matrix',
]

// Delimiters seen in the wild on printed material labels. This repo's own
// generators encode the bare SAP code (documents.py `_qr_png`), but the
// operator's older stickers carry `SAP|Description` — e.g.
// "1163|Cable Tie Wire ( Nylon)" — and sending that whole string as the SAP is
// what produced `no inventory item with SAP code '1163|Cable Tie Wire…'`.
const SCAN_DELIMS = /[|;\t\n\r]+/

/**
 * Ordered identifier candidates for a decoded scan, best guess first.
 *
 * Understands the payload shapes a label can plausibly carry: a bare code, a
 * `SAP:1163` / `MAT=GI-7001394` tagged pair, a delimited record whose first
 * field is the code, a URL with `?sap=`/`?code=` or a code as its last path
 * segment, and a small JSON object. Callers try them in order — nothing here
 * decides what a valid code IS, only what the string might contain.
 */
export function scanCandidates(decoded: string): string[] {
  const raw = (decoded || '').trim()
  if (!raw) return []
  const out: string[] = []
  const push = (v: unknown) => {
    const s = String(v ?? '').trim()
    if (s && !out.includes(s)) out.push(s)
  }

  // JSON payload — {"sap":"1163"} / {"material_code":"GI-7001394"}.
  if (raw.startsWith('{')) {
    try {
      const o = JSON.parse(raw) as Record<string, unknown>
      for (const k of ['sap', 'sap_code', 'SAP_Code', 'code', 'material_code', 'Material_Code']) {
        if (o[k] != null) push(o[k])
      }
    } catch { /* not JSON after all — fall through */ }
  }

  // URL payload — take the tagged query param, else the last path segment.
  if (/^https?:\/\//i.test(raw)) {
    try {
      const u = new URL(raw)
      for (const k of ['sap', 'sap_code', 'code', 'material', 'material_code']) {
        const v = u.searchParams.get(k)
        if (v) push(v)
      }
      const seg = u.pathname.split('/').filter(Boolean).pop()
      if (seg) push(decodeURIComponent(seg))
    } catch { /* malformed URL — fall through */ }
  }

  // "SAP: 1163" / "MAT=GI-7001394" anywhere in the text.
  const tagged = raw.match(/(?:sap|mat(?:erial)?)(?:[_ ]?code)?\s*[:=]\s*([A-Za-z0-9._/-]+)/i)
  if (tagged) push(tagged[1])

  // Delimited record: every field is a candidate, first field first — that is
  // where the code lives on every sticker layout we have seen.
  const fields = raw.split(SCAN_DELIMS).map((s) => s.trim()).filter(Boolean)
  if (fields.length > 1) fields.forEach(push)

  push(raw)                                   // the scan exactly as decoded
  // A comma-separated payload is ambiguous (descriptions contain commas), so
  // it is tried only after the whole string has had its chance.
  if (raw.includes(',')) raw.split(',').map((s) => s.trim()).forEach(push)
  return out
}

/** The single best identifier to send to the server for a decoded scan. */
export function parseScanPayload(decoded: string): string {
  return scanCandidates(decoded)[0] ?? (decoded || '').trim()
}

/** Decoded text → SAP code, or null when nothing matches.
 *  Tries: exact SAP · case-insensitive SAP · a "SAP:<code>" style payload ·
 *  a code embedded anywhere in the scan (labels often wrap the code). */
export function matchScanToSap(decoded: string, items: Row[]): string | null {
  const text = (decoded || '').trim()
  if (!text) return null
  // A delimited sticker payload ("1163|Cable Tie Wire") never equals a SAP
  // code, so resolve each field against the master before the fuzzy passes.
  const cands = scanCandidates(text)
  if (cands.length > 1) {
    const saps = items.map((r) => String(r.SAP_Code ?? '').trim()).filter(Boolean)
    for (const c of cands) {
      const hit = saps.find((s) => s.toLowerCase() === c.toLowerCase())
      if (hit) return hit
    }
  }
  const saps = items.map((r) => String(r.SAP_Code ?? '').trim()).filter(Boolean)
  const exact = saps.find((s) => s === text)
  if (exact) return exact
  const ci = saps.find((s) => s.toLowerCase() === text.toLowerCase())
  if (ci) return ci
  const m = text.match(/(?:sap|mat(?:erial)?)[:=\s]+([A-Za-z0-9_-]+)/i)
  if (m) {
    const tagged = saps.find((s) => s.toLowerCase() === m[1].toLowerCase())
    if (tagged) return tagged
  }
  // Longest SAP contained in the scanned text (avoid matching '1' in '1001').
  const contained = saps
    .filter((s) => s.length >= 4 && text.includes(s))
    .sort((a, b) => b.length - a.length)
  return contained[0] ?? null
}
