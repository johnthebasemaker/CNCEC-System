/**
 * QSEP quality gates, automated from MANUAL_TESTING_GUIDE.md §5.
 *
 * These are the guide's highest-value cases — TC-QC-02, TC-QC-16, TC-QC-17,
 * TC-QC-23 — chosen because each one fails SILENTLY and in the safe-looking
 * direction:
 *
 *   · a gate that leaked onto the other 430 materials would halt the site,
 *     and every individual refusal would look correct;
 *   · a fail-closed scope that turns into a wildcard shows an inspector MORE
 *     data, which nobody reports as a bug;
 *   · a clearance calculation that counted history would freeze controlled
 *     material forever, and the refusal message would still read plausibly.
 *
 * Fixtures (global-setup step 1d): SAP `E2EQC-1` in the Surface Shields
 * category, 100 units received at CNCEC, one PENDING inspection against it,
 * and three QC accounts covering both scoping axes plus the unbound case.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { QC_SITE } from '../harness/env'

const CONTROLLED = 'E2EQC-1'

/** TC-QC-17 — controlled material with no approved quantity cannot be issued. */
test('TC-QC-17: issuing an uninspected Surface Shield is refused, and the message names the site', async () => {
  const sk = await apiAs('sk', '60')
  const r = await sk.post('/entry/consumption', {
    data: {
      Date: new Date().toISOString().slice(0, 10),
      SAP_Code: CONTROLLED, Quantity: 5, Site_ID: QC_SITE,
      Work_Type: 'e2e', Issued_To: 'e2e',
    },
  })
  expect(r.status(), await r.text()).toBe(422)
  const body = await r.text()
  // Naming the site is the point: a generic "not allowed" would pass a laxer
  // assertion and would be a regression in the message, which is the only
  // part of this a store keeper ever sees.
  expect(body).toContain(QC_SITE)
  expect(body.toLowerCase()).toMatch(/quality|inspect/)
  await sk.dispose()
})

/**
 * TC-QC-02 — the NEGATIVE property, and the most important test in the file.
 * The gate covers 36 of 466 materials. If it ever generalises, every refusal
 * still looks individually reasonable and the whole site stops issuing.
 */
test('TC-QC-02: an UNCONTROLLED material is not touched by the quality gate', async () => {
  const sk = await apiAs('sk', '61')
  const r = await sk.post('/entry/consumption', {
    data: {
      Date: new Date().toISOString().slice(0, 10),
      SAP_Code: '1001', Quantity: 1, Site_ID: QC_SITE,
      Work_Type: 'e2e', Issued_To: 'e2e',
    },
  })
  // It may legitimately fail for its OWN reasons (stock, WBS, documents) —
  // what it must never do is fail for a QUALITY reason.
  const body = (await r.text()).toLowerCase()
  expect(body).not.toContain('surface shield')
  expect(body).not.toMatch(/quality inspection/)
  await sk.dispose()
})

/** TC-QC-24 — the quality block did not convert over-issue into a hard block. */
test('TC-QC-24: over-issuing an uncontrolled material is still allow-and-log', async () => {
  const sk = await apiAs('sk', '62')
  const r = await sk.post('/entry/consumption', {
    data: {
      Date: new Date().toISOString().slice(0, 10),
      SAP_Code: '1001', Quantity: 999999, Site_ID: QC_SITE,
      Work_Type: 'e2e over-issue', Issued_To: 'e2e',
    },
  })
  // Standing rule: the shelf is often right and the ledger often lags, so an
  // over-issue is recorded with a warning rather than refused. A 422 here
  // means somebody promoted the FEFO warning to an error.
  expect([200, 201]).toContain(r.status())
  await sk.dispose()
})

test.describe('QC dual scoping — one axis, never both, never neither', () => {
  /** TC-QC-14 — a site-bound QC sees its own site's queue. */
  test('TC-QC-14: a site-bound QC sees their site inspections', async () => {
    const qc = await apiAs('qc', '63')
    const r = await qc.get('/qc/inspections')
    expect(r.status()).toBe(200)
    const items = ((await r.json()) as { items: { Site_ID: string }[] }).items
    expect(items.length).toBeGreaterThan(0)
    expect(items.every((i) => i.Site_ID === QC_SITE)).toBe(true)
    await qc.dispose()
  })

  /** TC-QC-15 — a warehouse-bound QC sees no site rows at all. */
  test('TC-QC-15: a warehouse-bound QC sees no site-only inspections', async () => {
    const qc = await apiAs('qcwh', '64')
    const r = await qc.get('/qc/inspections')
    expect(r.status()).toBe(200)
    const items = ((await r.json()) as { items: { Site_ID: string | null }[] }).items
    expect(items.some((i) => i.Site_ID === QC_SITE)).toBe(false)
    await qc.dispose()
  })

  /**
   * TC-QC-16 — THE fail-closed test. An unbound QC must see an EMPTY list.
   * If this ever returns rows, stop and treat it as a live incident: it means
   * `''` is being read as "no filter" somewhere, which is the shape that hands
   * a misconfigured account every site in the company.
   */
  test('TC-QC-16: a QC bound to NEITHER a site nor a warehouse sees nothing', async () => {
    const qc = await apiAs('qcnone', '65')
    const r = await qc.get('/qc/inspections')
    expect(r.status()).toBe(200)
    const items = ((await r.json()) as { items: unknown[] }).items
    expect(items, 'an unbound QC must fail CLOSED — empty, never global').toHaveLength(0)
    await qc.dispose()
  })
})

/** TC-QC-13 — reading the queue is open; deciding is not. */
test('TC-QC-13: a store keeper cannot decide an inspection', async () => {
  const admin = await apiAs('admin', '66')
  const list = await (await admin.get('/qc/inspections?status=pending')).json() as {
    items: { id: number }[]
  }
  const id = list.items[0]?.id
  expect(id, 'the seeded pending inspection should exist').toBeTruthy()
  await admin.dispose()

  const sk = await apiAs('sk', '67')
  const r = await sk.post(`/qc/inspections/${id}/decide`, {
    data: { approved_qty: 100 },
  })
  expect(r.status()).toBe(403)
  await sk.dispose()
})

/**
 * TC-QC-08/20/23 — approve, then issue, in one flow.
 *
 * TC-QC-23 is embedded here and is the subtle one: CNCEC carries over a
 * thousand historical consumption rows that predate quality control entirely.
 * If clearance counted them, this approval would release nothing and the
 * issue below would still be refused — the site frozen by its own past.
 */
test('TC-QC-08 + TC-QC-20 + TC-QC-23: approving releases exactly that quantity, and history does not count against it', async () => {
  const admin = await apiAs('admin', '68')
  const pending = await (await admin.get('/qc/inspections?status=pending')).json() as {
    items: { id: number; SAP_Code: string }[]
  }
  const target = pending.items.find((i) => i.SAP_Code === CONTROLLED)
  expect(target, `a pending inspection for ${CONTROLLED} should be seeded`).toBeTruthy()

  // Admin outranks QC on require_roles("qc") — admin is always allowed.
  const decided = await admin.post(`/qc/inspections/${target!.id}/decide`, {
    data: { approved_qty: 10, reason: 'e2e partial approval' },
  })
  expect(decided.status(), await decided.text()).toBe(200)
  expect((await decided.json()).status).toBe('partially_approved')

  const clearance = await (await admin.get(
    `/qc/clearance?sap_code=${CONTROLLED}&site_id=${QC_SITE}`)).json() as {
      approved_qty: number; available_for_issue: number
    }
  expect(clearance.approved_qty).toBe(10)
  expect(
    clearance.available_for_issue,
    'historical consumption predating the first inspection must NOT be counted, '
    + 'or a site is frozen forever by its own past (TC-QC-23)',
  ).toBe(10)
  await admin.dispose()

  const sk = await apiAs('sk', '69')
  // Within the approved quantity → allowed.
  const ok = await sk.post('/entry/consumption', {
    data: {
      Date: new Date().toISOString().slice(0, 10),
      SAP_Code: CONTROLLED, Quantity: 10, Site_ID: QC_SITE,
      Work_Type: 'e2e', Issued_To: 'e2e',
    },
  })
  expect([200, 201], await ok.text()).toContain(ok.status())

  // TC-QC-21 — the staged issue counts against the approval immediately, in
  // the gap before an HOD approves it. Otherwise the same 10 units could be
  // promised twice.
  const second = await sk.post('/entry/consumption', {
    data: {
      Date: new Date().toISOString().slice(0, 10),
      SAP_Code: CONTROLLED, Quantity: 1, Site_ID: QC_SITE,
      Work_Type: 'e2e', Issued_To: 'e2e',
    },
  })
  expect(second.status(), 'staged counts as issued — see TC-QC-21').toBe(422)
  await sk.dispose()
})
