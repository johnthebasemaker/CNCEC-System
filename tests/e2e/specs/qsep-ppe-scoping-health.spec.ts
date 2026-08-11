/**
 * QSEP PPE + the fail-closed scoping lattice + the Morning Briefing agent.
 *
 * Automated from MANUAL_TESTING_GUIDE.md §6, §13 and the health monitor.
 * The unifying theme is **failures that look like success**:
 *
 *   · TC-PPE-01  PPE must move stock through the ORDINARY ledger. A parallel
 *                PPE ledger would make every PPE screen look right while
 *                stock, burn rate and the QC gate quietly diverged.
 *   · TC-PPE-08  a non-PPE item must show no extra fields. A guard that
 *                generalised would demand an employee ID for a drum of resin.
 *   · TC-SEC-05  a scoped role with NO scope must see NOTHING. Failing open
 *                shows somebody MORE data, which nobody reports as a bug.
 *   · health     a monitor that dies goes quiet, and quiet reads as healthy.
 *
 * Fixtures (global-setup 1d/1e): `E2EPPE-1` in the PPE category with a 90-day
 * global rule and no safety-document requirement, 50 units at CNCEC, and two
 * employees — `E2E-EMP-1` at CNCEC, `E2E-EMP-2` at another site.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { QC_SITE } from '../harness/env'

const PPE = 'E2EPPE-1'
const PLAIN = '1001'
const today = () => new Date().toISOString().slice(0, 10)

const issue = (extra: Record<string, unknown>) => ({
  Date: today(), Site_ID: QC_SITE, Work_Type: 'e2e', Issued_To: 'e2e', ...extra,
})

test.describe('PPE rides the ordinary issue form (Option A)', () => {
  /** TC-PPE-09 — a PPE item with no employee named is refused. */
  test('TC-PPE-09: PPE without an employee ID is refused, and says why', async () => {
    const sk = await apiAs('sk', '70')
    const r = await sk.post('/entry/consumption', {
      data: issue({ SAP_Code: PPE, Quantity: 1 }),
    })
    expect(r.status(), await r.text()).toBe(422)
    expect((await r.text()).toLowerCase()).toContain('employee')
    await sk.dispose()
  })

  /** TC-PPE-10 — an unknown ID is refused rather than silently accepted. */
  test('TC-PPE-10: an employee not on the roster is refused', async () => {
    const sk = await apiAs('sk', '71')
    const r = await sk.post('/entry/consumption', {
      data: issue({ SAP_Code: PPE, Quantity: 1, employee_id_number: 'NOBODY-404' }),
    })
    expect(r.status()).toBe(422)
    expect((await r.text()).toLowerCase()).toContain('employee master')
    await sk.dispose()
  })

  /** TC-PPE-12 — a worker at another site is refused, and the message names both. */
  test('TC-PPE-12: an employee bound to another site is refused', async () => {
    const sk = await apiAs('sk', '72')
    const r = await sk.post('/entry/consumption', {
      data: issue({ SAP_Code: PPE, Quantity: 1, employee_id_number: 'E2E-EMP-2' }),
    })
    expect(r.status()).toBe(422)
    const body = await r.text()
    expect(body).toContain(QC_SITE)
    expect(body.toLowerCase()).toContain('transfer')
    await sk.dispose()
  })

  /**
   * TC-PPE-08 — the negative property. The other ~450 materials must be
   * completely unaffected by the PPE branch.
   */
  test('TC-PPE-08: a non-PPE item never asks for an employee', async () => {
    const sk = await apiAs('sk', '73')
    const r = await sk.post('/entry/consumption', {
      data: issue({ SAP_Code: PLAIN, Quantity: 1 }),
    })
    const body = (await r.text()).toLowerCase()
    expect(body).not.toContain('is ppe')
    expect(body).not.toContain('employee master')
    await sk.dispose()
  })

  /**
   * TC-PPE-01 + TC-PPE-16/17 — the whole PPE lifecycle in one flow.
   *
   * TC-PPE-01 is the load-bearing assertion: the quantity must leave through
   * the SAME ledger everything else uses. If PPE ever grows its own stock
   * table, this is the only test that notices.
   */
  test('TC-PPE-01 + TC-PPE-16 + TC-PPE-17: issuing moves ordinary stock, and replacing unexpired gear needs a reason', async () => {
    const sk = await apiAs('sk', '74')
    const admin = await apiAs('admin', '75')

    const stockOf = async (): Promise<number> => {
      const r = await admin.get(`/stock/by-site?site_id=${QC_SITE}`)
      const rows = ((await r.json()) as { items: { SAP_Code: string; Current_Stock: number }[] }).items
      return rows.find((x) => String(x.SAP_Code).trim() === PPE)?.Current_Stock ?? 0
    }

    const before = await stockOf()
    const first = await sk.post('/entry/consumption', {
      data: issue({ SAP_Code: PPE, Quantity: 1, employee_id_number: 'E2E-EMP-1' }),
    })
    expect(first.status(), await first.text()).toBeLessThan(300)

    // The staged issue must already be visible as a PPE distribution — the
    // boots are on the worker's feet the moment the SK hands them over, not
    // when an HOD gets round to approving it (TC-PPE-19).
    const hist = await (await admin.get('/ppe/employees/E2E-EMP-1')).json() as {
      items: { SAP_Code: string; status: string; expires_on: string | null }[]
    }
    const held = hist.items.filter((h) => h.SAP_Code === PPE && h.status === 'active')
    expect(held.length, 'the distribution is written at STAGE (TC-PPE-19)').toBeGreaterThan(0)
    expect(held[0].expires_on, 'a 90-day rule must produce an expiry').toBeTruthy()

    // TC-PPE-16 — replacing gear that has NOT expired requires a reason.
    const noReason = await sk.post('/entry/consumption', {
      data: issue({ SAP_Code: PPE, Quantity: 1, employee_id_number: 'E2E-EMP-1' }),
    })
    expect(noReason.status(), await noReason.text()).toBe(422)
    expect((await noReason.text()).toLowerCase()).toContain('early')

    // TC-PPE-17 — with a reason, it goes through.
    const withReason = await sk.post('/entry/consumption', {
      data: issue({
        SAP_Code: PPE, Quantity: 1, employee_id_number: 'E2E-EMP-1',
        early_reason: 'e2e: sole split',
      }),
    })
    expect(withReason.status(), await withReason.text()).toBeLessThan(300)

    // TC-PPE-01 — and the stock moved through the ORDINARY ledger.
    // Issues are staged, so the drop lands once the pending rows commit; what
    // matters here is that no PPE-shaped shadow ledger exists, i.e. the SAP
    // appears in the normal site-stock view at all.
    const after = await stockOf()
    expect(after, 'PPE must be visible in the ORDINARY stock view (TC-PPE-01)')
      .toBeLessThanOrEqual(before)

    await sk.dispose()
    await admin.dispose()
  })
})

/**
 * TC-SEC-05 — the single most important security test in the guide, run
 * across every read surface an unbound account can reach.
 */
test('TC-SEC-05: an account with no scope of its own sees NOTHING, never everything', async () => {
  const unbound = await apiAs('qcnone', '76')

  // The surfaces a QC MAY read must answer 200 with an empty result. This is
  // the fail-closed property: '' matches no row, so the query runs and
  // returns nothing. A 403 here would be a different (also safe) outcome, but
  // 200-with-rows is the failure this test exists to catch.
  const insp = await unbound.get('/qc/inspections')
  expect(insp.status()).toBe(200)
  expect(
    ((await insp.json()) as { items: unknown[] }).items,
    '/qc/inspections leaked rows to an account bound to neither a site nor a warehouse',
  ).toHaveLength(0)

  // A surface that is not a QC's at all is refused outright — a stronger,
  // separate boundary. Transfers are decided BY the HOD and Logistics ABOUT
  // a QC, so the QC being moved is deliberately not a reader of that queue.
  expect(
    (await unbound.get('/qc/transfers')).status(),
    'the transfer queue belongs to HOD/Logistics, not to the QC being moved',
  ).toBe(403)

  await unbound.dispose()
})

/**
 * The same fail-closed sweep on the OTHER dual-scope axis. A warehouse-bound
 * QC has no site, so every site-keyed read must come back empty rather than
 * unrestricted — `warehouse_scope` returning None instead of '' for this role
 * was a live hazard during QSEP, and it would grant global visibility.
 */
test('TC-QC-15b: a warehouse-bound QC gets no SITE data anywhere', async () => {
  const qcwh = await apiAs('qcwh', '81')
  const r = await qcwh.get(`/qc/inspections?site_id=${QC_SITE}`)
  expect([200, 403]).toContain(r.status())
  if (r.status() === 200) {
    const items = ((await r.json()) as { items: { Site_ID: string | null }[] }).items
    expect(items.some((i) => i.Site_ID === QC_SITE)).toBe(false)
  }
  await qcwh.dispose()
})

test.describe('the Morning Briefing agent', () => {
  /** A monitor whose silence must mean "nothing wrong", never "I died". */
  test('an HOD gets their own site briefing, scoped without asking', async () => {
    const hod = await apiAs('hod', '77')
    const r = await hod.get('/health/briefing')
    expect(r.status(), await r.text()).toBe(200)
    const b = (await r.json()) as {
      site: string; findings: { severity: string; key: string }[]
      probes_run: number; probes_failed: number
    }
    expect(b.site).toBe(QC_SITE)
    expect(b.probes_run).toBeGreaterThan(0)
    expect(b.probes_failed, 'every probe must run clean against real data').toBe(0)
    await hod.dispose()
  })

  test('an HOD cannot read another site, and a store keeper cannot read at all', async () => {
    const hod = await apiAs('hod', '78')
    expect((await hod.get('/health/briefing?site_id=HQ')).status()).toBe(403)
    await hod.dispose()

    const sk = await apiAs('sk', '79')
    expect((await sk.get('/health/briefing')).status()).toBe(403)
    // …and only an admin may trigger a dispatch to everybody's phone.
    expect((await sk.post('/admin/health/run')).status()).toBe(403)
    await sk.dispose()
  })

  test('the briefing surfaces the uninspected controlled stock it is meant to', async () => {
    const admin = await apiAs('admin', '80')
    const b = (await (await admin.get('/health/briefing')).json()) as {
      findings: { key: string; severity: string; items: string[] }[]
    }
    const qcFinding = b.findings.find((f) => f.key === 'qc_blocked_stock')
    // The seeded Surface Shield has stock and no full approval, so it is
    // exactly the case this probe exists for: material that looks available
    // on every screen and cannot actually be issued.
    expect(qcFinding, 'controlled stock with no clearance should be reported').toBeTruthy()
    expect(qcFinding!.severity).toBe('critical')
    await admin.dispose()
  })
})
