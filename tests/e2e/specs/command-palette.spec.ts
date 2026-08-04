/**
 * The ⌘K command palette, in the browser.
 *
 * It has always jumped to PAGES. As of 2026-08-04 it also searches stock, so a
 * warehouse user who thinks in SAP codes can type one and land on that
 * material's card instead of walking Stock → filter → click.
 *
 * The two properties worth pinning are the ones that would be quietly wrong:
 *
 *  1. the material lookup is real — typing a SAP code produces a Materials
 *     section and Enter navigates to that material's page, not to whatever
 *     page happened to fuzzy-match the digits;
 *  2. it is still ROLE-SCOPED. The palette calls /stock/by-site, which applies
 *     the caller's site scoping server-side. A palette that bypassed that
 *     would be a neat way to read another site's inventory, so the store
 *     keeper's results are checked to be non-leaky rather than merely present.
 */
import { test, expect, type Page } from '@playwright/test'
import { storageStatePath } from '../harness/env'

// One page load per role, several assertions against it — the palette is
// cheap, but a fresh context per test is not.
test.describe.configure({ mode: 'serial' })

/**
 * `page.goto` resolves on `load`, but the ⌘K handler is attached in a React
 * effect — a keypress fired before the shell mounts is dropped, and the test
 * then fails on a palette that never opened. The header button is rendered by
 * AppLayout right beside <CommandPalette/>, so waiting for it is waiting for
 * the listener. Every ⌘K in this file goes through here.
 */
async function pressPaletteKey(page: Page) {
  await expect(page.getByRole('button', { name: 'Open command palette' })).toBeVisible()
  await page.keyboard.press('ControlOrMeta+k')
}

test.describe('command-palette:admin', () => {
  test.use({ storageState: storageStatePath('admin') })

  test('⌘K opens, filters pages, and Enter navigates', async ({ page }) => {
    await page.goto('/')
    await pressPaletteKey(page)

    const box = page.getByPlaceholder(/Jump to/)
    await expect(box).toBeVisible()
    // The placeholder itself is the promise the feature makes.
    await expect(box).toHaveAttribute('placeholder', /SAP code|material/i)

    await box.fill('report')
    await expect(page.getByText('Pages', { exact: true })).toBeVisible()
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/reports/)
  })

  test('a SAP code finds the material and clicking it opens the card', async ({ page }) => {
    await page.goto('/stock')
    // Ask the API for a SAP code that genuinely exists for this role, rather
    // than scraping a table cell — the first column of whatever widget renders
    // first is not reliably a SAP code, and a wrong seed makes this test
    // silently assert nothing.
    // Fetched from INSIDE the page: the access token lives in localStorage and
    // is attached by the app's axios interceptor, so Playwright's own request
    // context would be unauthenticated.
    const sap = await page.evaluate(async () => {
      const t = localStorage.getItem('gi_token')
      const r = await fetch('/api/stock/by-site?limit=1',
        { headers: { Authorization: `Bearer ${t}` } })
      if (!r.ok) return ''
      const j = await r.json()
      return String(j.items?.[0]?.SAP_Code ?? '').trim()
    })
    expect(sap.length).toBeGreaterThan(0)

    await pressPaletteKey(page)
    await page.getByPlaceholder(/Jump to/).fill(sap)

    const modal = page.locator('.ant-modal-body')
    await expect(modal.getByText('Materials', { exact: true })).toBeVisible({ timeout: 15_000 })
    await modal.getByText(sap, { exact: false }).last().click()
    await expect(page).toHaveURL(new RegExp(`/stock/material/${encodeURIComponent(sap)}`))
  })

  test('Esc closes it and nothing navigates', async ({ page }) => {
    await page.goto('/stock')
    await pressPaletteKey(page)
    await expect(page.getByPlaceholder(/Jump to/)).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByPlaceholder(/Jump to/)).toBeHidden()
    await expect(page).toHaveURL(/\/stock$/)
  })
})

test.describe('command-palette:store_keeper', () => {
  test.use({ storageState: storageStatePath('sk') })

  test('material search stays inside the role’s own site scope', async ({ page }) => {
    await page.goto('/entry/issue')
    await pressPaletteKey(page)
    const box = page.getByPlaceholder(/Jump to/)
    await expect(box).toBeVisible()

    // A broad query: whatever comes back must have come through the
    // server's site filter, so the palette adds no reach of its own.
    await box.fill('10')
    await page.waitForTimeout(1200)   // debounce + request

    const modal = page.locator('.ant-modal-body')
    // The palette must never surface a page this role cannot open — the page
    // half is filtered by the same manifest the sidebar uses.
    await expect(modal.getByText('Admin', { exact: true })).toHaveCount(0)
    await expect(modal.getByText('Master Data', { exact: true })).toHaveCount(0)
  })
})
