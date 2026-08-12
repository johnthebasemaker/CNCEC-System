/**
 * The Material Test Certificate rule, after the 2026-08-12 ruling moved it.
 *
 * It used to bind at RECEIPT and at DISPATCH. It now binds ONLY at issue, and
 * every test here exists because the wrong version of this rule fails in a way
 * that looks fine:
 *
 *   · a receipt block looks like diligence and is actually data loss — the
 *     material is in the yard either way, and refusing to record it makes real
 *     stock invisible to the shelf report, to planning, and to everyone;
 *   · a dispatch block looks like the same rule one hop later and produces the
 *     same stall, so removing only the first one fixes nothing;
 *   · a certificate that is not SHARED downstream means the site store keeper
 *     re-uploads a document Logistics already has — three copies of one PDF
 *     and three different "the" certificates for one lot.
 *
 * Fixtures (global-setup 1d): `E2EQC-1` is a Surface Shield WITH a certificate
 * at CNCEC; `E2EQC-2` is a Surface Shield with an APPROVED inspection and no
 * certificate anywhere, so the only thing blocking it is this rule.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { QC_SITE } from '../harness/env'

// SERIAL, and not as a convenience. The last test UPLOADS a certificate for
// `E2EQC-2`, permanently clearing the material every earlier test relies on
// being uncertified. Under the suite's `fullyParallel: true` that upload would
// race the refusal assertions, and the failure would be intermittent — the
// worst kind to debug and the easiest kind to dismiss as flake.
test.describe.configure({ mode: 'serial' })

const CERTIFIED = 'E2EQC-1'
const UNCERTIFIED = 'E2EQC-2'
const today = () => new Date().toISOString().slice(0, 10)

const line = (sap: string, qty: number) => ({
  Date: today(), SAP_Code: sap, Quantity: qty, Site_ID: QC_SITE,
  Work_Type: 'e2e', Issued_To: 'e2e',
})

/**
 * THE headline case, and the one the operator reported as a live blocker.
 * A truck arrives, the certificate is still in somebody's inbox, and the
 * warehouse must be able to say the material exists.
 */
test('TC-MTC-01: a Surface Shield can be RECEIVED with no certificate', async () => {
  const sk = await apiAs('sk', '82')
  const r = await sk.post('/entry/receipts', {
    data: {
      Date: today(), SAP_Code: UNCERTIFIED, Quantity: 5, Site_ID: QC_SITE,
      Supplier: 'e2e vendor',
    },
  })
  expect([200, 201], await r.text()).toContain(r.status())
  await sk.dispose()
})

/** The other half: what the receipt block was traded FOR. */
test('TC-MTC-02: …and then cannot be ISSUED, with a message naming all three ways to fix it', async () => {
  const sk = await apiAs('sk', '83')
  const r = await sk.post('/entry/consumption', { data: line(UNCERTIFIED, 1) })
  expect(r.status(), await r.text()).toBe(422)
  const body = await r.text()
  expect(body).toContain('Material Test Certificate')
  // The store keeper standing at the gate is usually not the person holding
  // the document. A refusal that does not say who can produce one sends them
  // hunting for a PDF that Logistics may already have on file.
  expect(body.toLowerCase()).toContain('purchase order')
  expect(body.toLowerCase()).toContain('delivery note')
  await sk.dispose()
})

/**
 * The QC gate is NOT what is blocking it — `E2EQC-2` has a fully approved
 * inspection. Without this assertion TC-MTC-02 would still pass if the two
 * gates were accidentally fused back together.
 */
test('TC-MTC-03: the refusal is the PAPERWORK gate, not the inspection gate', async () => {
  const admin = await apiAs('admin', '84')
  const c = await (await admin.get(
    `/qc/clearance?sap_code=${UNCERTIFIED}&site_id=${QC_SITE}`)).json() as {
      controlled: boolean; mtc_ok: boolean; approved_qty: number
      available_for_issue: number; blocked: boolean
    }
  expect(c.controlled).toBe(true)
  expect(c.approved_qty, 'the inspection half is satisfied').toBeGreaterThan(0)
  expect(c.available_for_issue).toBeGreaterThan(0)
  expect(c.mtc_ok, 'the paperwork half is not').toBe(false)
  expect(c.blocked, 'either half failing must block the form').toBe(true)
  await admin.dispose()
})

/** A material WITH a certificate reports the fact, and says where it came from. */
test('TC-MTC-04: a certified material reports its source, so the clearance is auditable', async () => {
  const admin = await apiAs('admin', '85')
  const c = await (await admin.get(
    `/qc/clearance?sap_code=${CERTIFIED}&site_id=${QC_SITE}`)).json() as {
      mtc_ok: boolean; mtc_source: string; mtc_label: string
    }
  expect(c.mtc_ok).toBe(true)
  expect(c.mtc_source).toBe('site')
  // "Cleared by some certificate" is not an audit trail.
  expect(String(c.mtc_label ?? '')).not.toHaveLength(0)
  await admin.dispose()
})

/** The negative property: the other ~450 materials never hear about any of this. */
test('TC-MTC-05: an ordinary material is never asked for a certificate', async () => {
  const admin = await apiAs('admin', '86')
  const c = await (await admin.get(
    `/qc/clearance?sap_code=1001&site_id=${QC_SITE}`)).json() as {
      controlled: boolean; mtc_ok: boolean; blocked: boolean
    }
  expect(c.controlled).toBe(false)
  expect(c.mtc_ok).toBe(true)
  expect(c.blocked).toBe(false)

  const sk = await apiAs('sk', '87')
  const r = await sk.post('/entry/consumption', { data: line('1001', 1) })
  expect((await r.text())).not.toContain('Material Test Certificate')
  await sk.dispose()
  await admin.dispose()
})

/**
 * Cross-role upload + downstream inheritance in one flow — requirements 3 and
 * 4 of the ruling. Logistics (who never sets foot on the site) files the
 * certificate, and the site store keeper is cleared by it without touching it.
 */
test('TC-MTC-06: Logistics uploads the certificate and the SITE inherits it', async () => {
  const sk = await apiAs('sk', '88')
  const before = await sk.post('/entry/consumption', { data: line(UNCERTIFIED, 1) })
  expect(before.status(), 'precondition: still blocked').toBe(422)

  const log = await apiAs('logistics', '89')
  const up = await log.post('/entry/mtc', {
    multipart: {
      file: { name: 'mtc.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 e2e') },
      sap_code: UNCERTIFIED,
      site_id: QC_SITE,
      mtc_number: 'E2E-MTC-LOGISTICS',
    },
  })
  expect(up.status(), await up.text()).toBe(201)
  await log.dispose()

  // The SK uploaded nothing, and is now cleared.
  const after = await sk.post('/entry/consumption', { data: line(UNCERTIFIED, 1) })
  expect([200, 201], await after.text()).toContain(after.status())
  await sk.dispose()
})
