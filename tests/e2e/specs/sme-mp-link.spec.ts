/**
 * The bridge from the SME Session Builder to the manpower report
 * (Phase 8 slice 8e).
 *
 * Suite CI proves the arithmetic — conservation, the missing headcount on the
 * blocked column, the cache. What it cannot prove is the HANDOFF: that the
 * button exists, that it refuses an empty session, that the session survives
 * the trip through the URL, and that it lands on the right tab of a different
 * route with its own provider tree.
 *
 * The handoff is the part most likely to break silently. `ScenarioProvider` is
 * mounted inside SmePage; Labor Tracking is a different route and cannot read
 * it, so the session travels as `?scenario=` and is decoded on arrival. A
 * mismatch between the two ends produces an empty report rather than an error.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'
import { openTab } from '../harness/ui'

test.use({ storageState: storageStatePath('hod') })
test.describe.configure({ mode: 'serial' })

async function openBuilder(page: import('@playwright/test').Page) {
  await page.goto('/sme')
  await openTab(page, /Session Builder/)
  // Wait for the SNAPSHOT, not just the card: the equipment picker is empty
  // until the model loads, and clicking an empty select proves nothing.
  await expect(page.getByText(/Find equipment/i)).toBeVisible()
  await expect(page.locator('.ant-select')
    .filter({ hasText: /Pick equipment tags/ })).toBeVisible()
}

const MPH = /Session Report For MP&H/

test('the button refuses an empty session rather than producing a report of zeroes',
  async ({ page }) => {
    await openBuilder(page)
    // A fresh account has no session. Costing nothing in labour is not a
    // report, so the control is disabled and says why on hover.
    const btn = page.getByRole('button', { name: MPH })
    await expect(btn).toBeVisible()
    await expect(btn).toBeDisabled()
  })

test('the session survives the trip through the URL and lands on its own tab',
  async ({ page }) => {
    await openBuilder(page)

    // Fill the session through the location bulk-add rather than the tag
    // picker: one click, no dropdown left open over the next control, and it
    // is the path somebody filling a session by area actually takes.
    await page.getByRole('button', { name: /Add all/ }).first().click()
    await expect(page.getByRole('button', { name: /Clear all/ })).toBeEnabled()

    // ── the exact round trip ────────────────────────────────────────────
    // ScenarioContext writes the session into ?scenario= on this page, and
    // the button hands the SAME string to the other route. Comparing the two
    // is a stronger assertion than checking one tag survived: it catches an
    // encoder that drops, reorders or re-escapes.
    const smeScenario = new URL(page.url()).searchParams.get('scenario')
    expect(smeScenario, 'the SME page must publish the session in its URL')
      .toBeTruthy()

    const btn = page.getByRole('button', { name: MPH })
    await expect(btn).toBeEnabled()
    await btn.click()

    await expect(page).toHaveURL(/\/manhours\?/)
    const mh = new URL(page.url()).searchParams
    expect(mh.get('tab')).toBe('session')
    expect(mh.get('scenario'), 'the session must arrive byte-for-byte')
      .toBe(smeScenario)

    // Landed on the right tab of a page with twelve of them…
    await expect(page.getByRole('tab', { name: /SME Session/ }))
      .toHaveAttribute('aria-selected', 'true')
    // …and the tags DECODED into the page's own state. "Cost it" is disabled
    // on an empty order, so an enabled button is the decode working; the chips
    // themselves collapse to "+N" at this width and are not worth asserting.
    await expect(page.getByRole('button', { name: /Cost it/ })).toBeEnabled()
    expect(await page.locator('.ant-select-selection-item').count())
      .toBeGreaterThan(0)
  })

test('the three columns are there, and Blocked shows no headcount', async ({ page }) => {
  // Reached directly by URL, which is what a shared link does.
  await page.goto('/manhours?tab=session&scenario=0050')
  await expect(page.getByRole('tab', { name: /SME Session/ }))
    .toHaveAttribute('aria-selected', 'true')

  await page.getByRole('button', { name: /Cost it/ }).click()

  await expect(page.getByText('We can do now')).toBeVisible()
  await expect(page.getByText('Overall total')).toBeVisible()
  await expect(page.getByText('Blocked by material')).toBeVisible()

  // ⚠️ THE RULE THIS GUARDS. A headcount printed against blocked work is a
  // headcount somebody hires against, and they are idle when the drums land.
  // If the "not applicable" chip is ever "helpfully" replaced with a number,
  // this fails.
  await expect(page.getByText(
    /you cannot deploy labour against material that has not arrived/))
    .toBeVisible()
  await expect(page.getByText('not applicable').first()).toBeVisible()
})

test('the page says surface prep is not in this report', async ({ page }) => {
  // Blasting consumes no recipe line, so the material model has no opinion on
  // whether it is blocked. Silently omitting it would read as "no prep needed".
  await page.goto('/manhours?tab=session&scenario=0050')
  await expect(page.getByText(/Surface prep is not included/)).toBeVisible()
})
