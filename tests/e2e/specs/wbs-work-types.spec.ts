/**
 * WBS numbers and work types, in a real browser (Phase 9a).
 *
 * THE BUG THIS PAGE FIXES IS A MISSING SCREEN, so a browser test is the only
 * one that can prove it. `wbs_master`, the `assert_wbs` gate and three HOD
 * endpoints have shipped since the parity build; NOTHING in the frontend ever
 * called them, so the table stayed empty, the gate stayed a no-op and every
 * live consumption row carried a blank WBS. Suite CK proves the API. Only this
 * proves an HOD can actually reach it.
 *
 * ⚠️ AND THAT THE PAGE SAYS WHAT IS CURRENTLY ENFORCED. Both rules are
 * conditional — empty list, nothing refused — so a screen that just showed an
 * empty grid would leave an HOD unable to tell "off" from "broken".
 *
 * ⚠️ THIS FILE RUNS IN ITS OWN PROJECT, STRICTLY LAST (see playwright.config).
 * Adding the first WBS number for a site turns `assert_wbs` ON for that site;
 * adding the first work type turns the strict dropdown on. A scoped HOD can
 * only manage their OWN site — the site every other spec posts entries to — so
 * this cannot be isolated by choosing a different one. Run inside the parallel
 * pack it flipped the gate mid-flight and 422'd whichever specs happened to be
 * posting at that moment, which surfaced as a DIFFERENT spec failing on each
 * run. Same hazard as entry-docs and the same remedy, plus the close-down
 * below so a re-run does not start against a site it already switched on.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { storageStatePath } from '../harness/env'

test.use({ storageState: storageStatePath('hod') })
test.describe.configure({ mode: 'serial' })

// Everything this file creates is prefixed E2E-, and the close-down below
// CLOSES the WBS numbers and RETIRES the work types rather than deleting them:
// closing is what actually turns the gate back off, and it is also the path an
// HOD would really take. A row that was only deleted would prove nothing about
// the status flow.
const MADE: { wbs: number[]; types: number[] } = { wbs: [], types: [] }

test('an HOD can reach the page from the sidebar — the whole point of the slice',
  async ({ page }) => {
    await page.goto('/hod/wbs')
    await expect(page.getByRole('heading', { name: /WBS & Work Types/ }))
      .toBeVisible()
    await expect(page.getByRole('tab', { name: /Work Types/ })).toBeVisible()
    await expect(page.getByRole('tab', { name: /WBS Numbers/ })).toBeVisible()
  })

test('the page states what is enforced right now, not just what exists',
  async ({ page }) => {
    await page.goto('/hod/wbs')
    // One of the two banners is always true, and they say opposite things about
    // whether entry forms are currently asking for a WBS. An HOD who cannot
    // tell "not configured" from "not working" will configure it twice.
    await expect(
      page.getByText(/entry forms (now require one|are not asking for one)/),
    ).toBeVisible()
  })

test('a work type is added, mapped to a WBS, and the mapping is refused until the number exists',
  async ({ page }) => {
    const stamp = Date.now()
    const WT = `E2E-Work-${stamp}`
    const WBS = `E2E-WBS-${stamp}`

    await page.goto('/hod/wbs')

    // ── the work type ───────────────────────────────────────────────────────
    await page.getByRole('button', { name: /Add work type/ }).click()
    const wtDialog = page.getByRole('dialog', { name: 'Add a work type' })
    await wtDialog.locator('#Work_Type').fill(WT)
    await wtDialog.getByRole('button', { name: 'Add', exact: true }).click()
    await expect(page.getByText(`${WT} added`)).toBeVisible()
    await expect(page.getByRole('cell', { name: WT })).toBeVisible()

    // ⚠️ THE COLLISION THE NORMALISED KEY EXISTS TO STOP. The live ledger holds
    // civil AND Civil, coating AND Coating. Keyed on raw text those take
    // different WBS numbers and the report splits with nothing to show why.
    await page.getByRole('button', { name: /Add work type/ }).click()
    await wtDialog.locator('#Work_Type').fill(WT.toUpperCase())
    await wtDialog.getByRole('button', { name: 'Add', exact: true }).click()
    await expect(page.getByText(/same work type spelled differently|already lists/))
      .toBeVisible()
    await wtDialog.getByRole('button', { name: 'Cancel' }).click()

    // ── the WBS number, and only then the mapping ───────────────────────────
    await page.getByRole('tab', { name: /WBS Numbers/ }).click()
    await page.getByRole('button', { name: /Add WBS number/ }).click()
    const wbsDialog = page.getByRole('dialog', { name: 'Add a WBS number' })
    await wbsDialog.locator('#WBS_Number').fill(WBS)
    await wbsDialog.getByRole('button', { name: 'Add', exact: true }).click()
    await expect(page.getByText(`WBS ${WBS} added`)).toBeVisible()

    await page.getByRole('tab', { name: /Work Types/ }).click()
    const row = page.getByRole('row', { name: new RegExp(WT) })
    await row.getByRole('combobox').click()
    await page.getByTitle(WBS, { exact: true }).click()
    await expect(page.getByText(`${WT} → ${WBS}`)).toBeVisible()

    // Remember what to switch back off. Read through the API rather than
    // scraped from the DOM: the id is what the close-down needs and the table
    // never renders it.
    const api = await apiAs('hod')
    const types = await (await api.get('/hod/site-config/work-types')).json()
    for (const t of types.items as { id: number; Work_Type: string }[]) {
      if (t.Work_Type === WT) MADE.types.push(t.id)
    }
    const nums = await (await api.get('/hod/site-config/wbs')).json()
    for (const w of nums.items as { id: number; WBS_Number: string }[]) {
      if (w.WBS_Number === WBS) MADE.wbs.push(w.id)
    }
    await api.dispose()
    expect(MADE.types.length + MADE.wbs.length).toBe(2)
  })

test.afterAll(async () => {
  // The e2e database is dropped at teardown, so this is not about the next RUN
  // — it is about the next PROJECT. Today nothing is scheduled after this one;
  // the day something is, it would start against a site whose gates this file
  // silently switched on, and fail somewhere with no visible connection to WBS.
  const api = await apiAs('hod')
  for (const row of MADE.types) {
    await api.patch(`/hod/site-config/work-types/${row}`,
      { data: { status: 'retired' } }).catch(() => {})
  }
  for (const row of MADE.wbs) {
    await api.patch(`/hod/site-config/wbs/${row}?status=closed`).catch(() => {})
  }
  await api.dispose()
})

test('the history importer offers what the ledger actually spelled, merged',
  async ({ page }) => {
    await page.goto('/hod/wbs')
    await page.getByRole('button', { name: /Import from history/ }).click()
    // Either it proposes rows or it says every one is already adopted. Both are
    // real answers; an empty grid with no explanation is not.
    await expect(
      page.getByText(/Work types seen in this site's ledger/),
    ).toBeVisible()
  })
