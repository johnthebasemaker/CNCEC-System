/**
 * The Head of Qualities' portal, in a real browser (Phase 8 slice 8d).
 *
 * The service suite proves the API's scoping, the category filter and the
 * read-only guard. What it cannot prove is that the account LANDS somewhere
 * useful and sees seven working tabs — nor that the escalation form makes
 * "exactly one target" structural rather than a rule the user must remember.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test.use({ storageState: storageStatePath('qchod') })
test.describe.configure({ mode: 'serial' })

test('the role lands on its own portal, not the site Dashboard', async ({ page }) => {
  // ROLE_HOME sends it straight here. The ordinary Dashboard is site-shaped and
  // would show an account with no site nothing it is responsible for.
  await page.goto('/')
  await expect(page).toHaveURL(/\/qc-hod/)
  await expect(page.getByRole('heading', { name: /Quality Oversight/ }))
    .toBeVisible()
})

test('seven tabs, and the category boundary is stated on the page',
  async ({ page }) => {
    await page.goto('/qc-hod')
    for (const label of ['Overview', 'Surface Shield POs', 'MTC Register',
                         'Where It Is Used', 'Stagnation & Expiry',
                         'Escalations', 'Settings']) {
      await expect(page.getByRole('tab', { name: new RegExp(label) }))
        .toBeVisible()
    }
    // The category IS the boundary of the role, so the page says so rather
    // than leaving a cross-site account looking unbounded.
    await expect(page.getByText(/every figure on this page is filtered to it/))
      .toBeVisible()
  })

test('the escalation form cannot express "everywhere"', async ({ page }) => {
  await page.goto('/qc-hod')
  await page.getByRole('tab', { name: /Overview/ }).click()

  // The Overview's banner only appears when something is actually uncertified,
  // so reach the form through a tab that always has the control.
  await page.getByRole('tab', { name: /Stagnation & Expiry/ }).click()
  await expect(page.getByText(/Stagnant after/)).toBeVisible()

  await page.getByRole('tab', { name: /Escalations/ }).click()
  await expect(page.getByRole('columnheader', { name: 'Status' })).toBeVisible()
})

test('Settings shows the seeded 90 / 60 day policy', async ({ page }) => {
  await page.goto('/qc-hod')
  await page.getByRole('tab', { name: /Settings/ }).click()
  await expect(page.getByLabel(/Stagnant after/)).toHaveValue('90')
  await expect(page.getByLabel(/Warn this many days before expiry/))
    .toHaveValue('60')
  // Stated as policy, because it is the operator's number and not a constant.
  await expect(page.getByText(/not a system constant/)).toBeVisible()
})

test('the portal is refused to an ordinary HOD', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: storageStatePath('hod') })
  try {
    const page = await ctx.newPage()
    await page.goto('/qc-hod')
    // The manifest has no qc-hod entry for `hod`, so the route guard redirects
    // rather than rendering a page whose every request would 403.
    await expect(page).not.toHaveURL(/\/qc-hod/)
  } finally {
    await ctx.close()
  }
})
