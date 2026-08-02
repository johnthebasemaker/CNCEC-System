/**
 * SME strict tier segregation, in the browser.
 *
 * The locked rule: "Feasibility judges the physical tier only. A tank cannot
 * be built with a purchase order."
 *
 * The bug this pins, reported 2026-08-03: PHENACIN ACP POWDER held 0 available
 * and 56,350 on order, and the SME portal showed the systems that depend on it
 * as ready to build. The ENGINE was right all along (SQM_Achievable_Now = 0,
 * status 🔴 Blocked); every presentation layer above it re-derived coverage as
 * `Allocated_Qty / Demand_Qty` — and `Allocated_Qty` is physical stock PLUS
 * stock on order. Measured on the live snapshot: 18 of 85 (tag, code) units
 * showed a green 100% "Fully Ready" pill they had not earned, and buildable
 * area was overstated by 9,118 m² (21.5% of the remaining programme).
 *
 * The fixture (seeded in global-setup.ts) reproduces that shape exactly:
 * E2E-TIER-TANK, 100 m² remaining on system code 9101, blocked by
 * E2ETIER-ACP — 0 on the shelf, 5,000 on an open PO. Ready-now must read
 * 0.0% and with-ordered 100.0%, as two separate figures.
 *
 * Suite BB in service_tests.py pins the same case at the engine, export and
 * executive-summary layers; this spec is the half only a browser can prove.
 *
 * NOTE: the master grid is antd `virtual`, so off-screen rows are not in the
 * DOM. These assertions deliberately target the per-code expander and the KPI
 * strip, which always render, rather than hunting a virtualized row.
 */
import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { storageStatePath } from '../harness/env'

const CODE = '9101'

test.describe('sme-tiers:hod', () => {
  test.use({ storageState: storageStatePath('hod') })

  /** Open the tab and return ITS panel — antd keeps every visited tab mounted,
   *  so page-wide text locators hit the Dashboard's copy too. */
  async function totalOverview(page: Page) {
    await page.goto('/sme')
    await page.getByRole('tab', { name: /Total Overview/ }).click()
    const panel = page.getByRole('tabpanel', { name: /Total Overview/ })
    await expect(panel.getByText('MASTER TABLE — EQUIPMENT × SYSTEM CODE')).toBeVisible()
    return panel
  }

  test('the grid gives each tier its own column', async ({ page }) => {
    const panel = await totalOverview(page)

    // The legend that makes the distinction readable at all.
    await expect(panel.getByText('Available stock and stock on order are counted separately'))
      .toBeVisible()

    // One merged "Allocated" + "Fulfil %" pair was the shape that hid the bug.
    // Header cells also contain a filter button, so match on their TEXT.
    const head = panel.locator('.ant-table-thead').first()
    const titles = (await head.locator('th').allInnerTexts()).map((t) => t.trim())
    for (const name of ['Available', 'On Order', 'Short (physical)',
      'To buy (net)', 'Ready now %', 'With ordered %']) {
      expect(titles, `the grid must have a "${name}" column — got ${titles.join(' · ')}`)
        .toContain(name)
    }
    // …and must NOT still carry the merged one.
    expect(titles, 'the merged "Allocated" column must be gone').not.toContain('Allocated')
  })

  test('the KPI strip reports buildable-now apart from with-ordered',
    async ({ page }) => {
      const panel = await totalOverview(page)
      await expect(panel.getByText('Buildable now SQM').first()).toBeVisible()
      await expect(panel.getByText('With ordered SQM').first()).toBeVisible()
      await expect(panel.getByText('Avg Coverage (now)')).toBeVisible()
      // The old single "Available Coverage SQM" KPI showed the with-ordered
      // number under a physical-sounding name.
      await expect(page.getByText('Available Coverage SQM')).toHaveCount(0)
    })

  test('a unit covered only by a purchase order reads 0% ready, 100% with ordered',
    async ({ page }) => {
      const panel = await totalOverview(page)
      const header = panel.locator('.ant-collapse-header')
        .filter({ hasText: `Code ${CODE}` })
      await expect(header).toBeVisible()

      // THE assertion. The pill is the physical figure; the amber hint is the
      // forecast. Before the fix the pill itself read 100.0%.
      await expect(header, 'the readiness pill must show the PHYSICAL 0.0%')
        .toContainText('0.0%')
      await expect(header, 'the forecast must be labelled, not merged into the pill')
        .toContainText('100.0% with ordered')
    })

  test('the per-code material table separates stock available from stock on order',
    async ({ page }) => {
      const tab = await totalOverview(page)
      const header = tab.locator('.ant-collapse-header')
        .filter({ hasText: `Code ${CODE}` })
      await header.click()

      const panel = tab.locator('.ant-collapse-item-active')
      await expect(panel.getByRole('columnheader', { name: 'Stock available' })).toBeVisible()
      await expect(panel.getByRole('columnheader', { name: 'Stock on order' })).toBeVisible()

      // The blocked component still REPORTS its 5,000 on order — the quantity
      // is never hidden, it just never counts as readiness.
      const acp = panel.locator('.ant-table-row').filter({ hasText: 'E2ETIER-ACP' })
      await expect(acp).toBeVisible()
      const cells = (await acp.locator('td').allInnerTexts()).join(' | ')
      expect(cells, `ACP row read: ${cells}`).toContain('5,000')
      expect(cells, 'ready-now must be 0.0% with nothing on the shelf').toContain('0.0%')
      expect(cells, 'with-ordered must be 100.0%').toContain('100.0%')

      // The fully-stocked sibling is the control: it IS ready today.
      const ok = panel.locator('.ant-table-row').filter({ hasText: 'E2ETIER-OK' })
      await expect(ok).toBeVisible()
      const okCells = (await ok.locator('td').allInnerTexts()).join(' | ')
      expect(okCells, `OK row read: ${okCells}`).toContain('100.0%')
    })
})
