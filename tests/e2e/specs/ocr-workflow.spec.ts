/**
 * The paper-first consumption workflow, in a real browser (Phase 9d).
 *
 * Suite CN proves the state machine, the double-deduction guard, the QSEP
 * override and the four layers. What it cannot prove is that the three people
 * who use this every day can each SEE their own step — and, just as important,
 * that the page no longer tells them the old order.
 *
 * ⚠️ THE COLOURS ARE THE CONTROL, so they are worth a browser test. Phase 5's
 * protection was structural: the supervisor's form had no material field. That
 * is gone, and what replaces it is visible only if it renders — a trail that
 * silently stops showing the store keeper's correction would leave an HOD
 * approving a number they think the supervisor wrote.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { storageStatePath } from '../harness/env'

test.describe.configure({ mode: 'serial' })

// ⚠️ THIS FILE SEEDS SHARED MASTER DATA (one recipe line and one benchmark) so
// its most important check cannot skip for want of a material-backed system.
// Master data is read by the SME cascade, which every SME spec renders — so
// what it adds, it takes away. The same hazard as wbs-work-types, one level
// milder: this does not switch a gate on, it only makes the grid bigger.
const SEEDED: { recipes: number[]; norms: number[] } = { recipes: [], norms: [] }

test.afterAll(async () => {
  const hod = await apiAs('hod')
  for (const id of SEEDED.recipes) {
    await hod.delete(`/sme/master/recipes/${id}`).catch(() => {})
  }
  for (const id of SEEDED.norms) {
    await hod.delete(`/sme/master/manpower-norms/${id}`).catch(() => {})
  }
  await hod.dispose()
})

test('the page describes the NEW order, not the store-keeper-first one',
  async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath('supervisor') })
    const page = await ctx.newPage()
    await page.goto('/execution')
    // The old copy said the store keeper records what left the store FIRST,
    // and that approval posts the area. Both changed in 9d, and a page that
    // still says otherwise teaches the wrong workflow to every new starter.
    await expect(page.getByText(/supervisor fills a printed form in the field/))
      .toBeVisible()
    await expect(page.getByText(/deducts\s+the material/)).toBeVisible()
    await ctx.close()
  })

for (const role of ['supervisor', 'hod', 'sk'] as const) {
  test(`a ${role} can reach the upload card`, async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath(role) })
    const page = await ctx.newPage()
    await page.goto('/execution')
    await expect(page.getByText('Upload a filled form')).toBeVisible()
    // ⚠️ THE TWO RULES A SUPERVISOR MOST NEEDS AND IS LEAST LIKELY TO GUESS.
    await expect(page.getByText(/whole page including the QR code/)).toBeVisible()
    await expect(page.getByText(/blank rather than guessed/)).toBeVisible()
    await ctx.close()
  })
}

test('an unreadable upload fails loudly instead of creating an empty entry',
  async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath('supervisor') })
    const page = await ctx.newPage()
    await page.goto('/execution')

    // A file that is not an image at all. The point is not the 415 — it is that
    // nothing is created. A blank draft looks merely unfilled, and a blank
    // draft submitted is a consumption of zero silently recorded.
    const api = await apiAs('supervisor')
    const before = await (await api.get('/execution/entries')).json()
    const r = await api.post('/execution/ocr/upload', {
      multipart: {
        file: { name: 'notes.txt', mimeType: 'text/plain',
                buffer: Buffer.from('this is not a form') },
      },
    })
    expect(r.status()).toBe(415)
    expect(await r.text()).toMatch(/JPG/)
    const after = await (await api.get('/execution/entries')).json()
    expect(after.items.length).toBe(before.items.length)
    await api.dispose()
    await ctx.close()
  })

test('supervisor files → store keeper verifies in red → HOD sees who changed what',
  async ({ browser }) => {
    const sup = await apiAs('supervisor')
    const sk = await apiAs('sk')
    const hod = await apiAs('hod')

    // ⚠️ SEED THE FIXTURE RATHER THAN HUNTING FOR ONE. A test that skips when
    // the seed data happens not to contain a material-backed system proves
    // nothing on the day it matters — and this is the check that the four
    // layers actually render.
    const CODE = 'E2E-OCR-SYS'
    const ESC = 'E2E-OCR-ESC'
    const rec = await hod.post('/sme/master/recipes', {
      data: { Lining_System_Code: CODE, Execution_Sub_Activity_Code: ESC,
              Material_Code: 'E2E-MAT', SAP_Code: 'E2E-SAP', For_1_SQM: 2,
              Lining_System_Name: 'E2E OCR system', Material_Name: 'E2E Mortar',
              UOM: 'KG' },
    })
    if (rec.ok()) SEEDED.recipes.push((await rec.json()).id)
    const nrm = await hod.post('/sme/master/manpower-norms', {
      data: { Type: 'CV', Lining_System_Code: CODE,
              Execution_Sub_Activity_Code: ESC, Activity: 'e2e ocr lining',
              Crew_Size: 1, Hours_Per_Shift: 11, Manhours_Per_Shift: 11,
              Standard_Productivity_Per_Shift: 100, Crew: { MASON: 1 } },
    })
    if (nrm.ok()) SEEDED.norms.push((await nrm.json()).id)

    const opened = await sup.post('/execution/entries', {
      data: {
        work_date: new Date().toISOString().slice(0, 10),
        equipment_tag: 'E2E-OCR-T1',
        lining_system_code: CODE,
        execution_sub_activity_code: ESC,
        materials: [{ Material_Code: 'E2E-MAT', SAP_Code: 'E2E-SAP',
                      Actual_Qty: 0, UOM: 'KG' }],
      },
    })
    expect(opened.status()).toBe(201)
    const entry = await opened.json()
    expect(entry.status).toBe('DRAFT_SUPERVISOR')

    const full = await (await sup.get(`/execution/entries/${entry.id}`)).json()
    const lineId = full.materials[0].id

    const filed = await sup.post(`/execution/entries/${entry.id}/supervisor`, {
      data: {
        actual_sqm: 10,
        manpower: [{ Role_Code: 'MASON', Headcount: 1, Hours: 8 }],
        material_variance_reason: 'e2e',
        manpower_variance_reason: 'e2e',
        materials: [{ id: lineId, Actual_Qty: 22, Lot_No: 'E2E-LOT-1' }],
      },
    })
    expect(filed.status()).toBe(200)
    // ⚠️ THE STORE KEEPER, NOT THE HOD. This is the inversion.
    expect((await filed.json()).status).toBe('PENDING_SK')

    // An edit with no reason is refused — the HOD is about to approve a number
    // the supervisor did not write.
    const noReason = await sk.post(`/execution/entries/${entry.id}/sk-verify`, {
      data: { materials: [{ id: lineId, Actual_Qty: 18 }] },
    })
    expect(noReason.status()).toBe(422)

    const verified = await sk.post(`/execution/entries/${entry.id}/sk-verify`, {
      data: { materials: [{ id: lineId, Actual_Qty: 18 }],
              reason: 'only 18 left the store' },
    })
    expect(verified.status()).toBe(200)
    expect((await verified.json()).status).toBe('PENDING_HOD')

    // ── the trail, on the HOD's screen ────────────────────────────────────
    const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
    const page = await ctx.newPage()
    await page.goto('/execution')
    await page.getByRole('row', { name: new RegExp(entry.Entry_No) })
      .getByRole('button', { name: 'Review' }).click()

    await expect(page.getByText(/Store keeper/)).toBeVisible()
    await expect(page.getByText('only 18 left the store')).toBeVisible()
    // Amber then red: the supervisor moved it, then the store keeper did. If
    // this ever renders as a single number, the four layers have stopped being
    // a control and become a column nobody reads.
    await expect(page.getByText('→ 18')).toBeVisible()

    await ctx.close()
    await sup.dispose(); await sk.dispose(); await hod.dispose()
  })
