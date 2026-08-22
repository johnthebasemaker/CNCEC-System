/**
 * Responsive app shell.
 *
 * The rule this pins: on a phone the navigation is an OVERLAY, never a column.
 * antd's zero-width Sider trigger re-opens the rail IN FLOW, which squeezed the
 * content div on mobile — so below `md` the shell renders a Drawer instead and
 * the header grows a hamburger. Desktop keeps the sticky rail.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1280, height: 800 }

test.describe('responsive:hod', () => {
  test.use({ storageState: storageStatePath('hod') })

  test('phone: nav is a drawer over the content, not a column beside it', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await page.goto('/')
    const burger = page.getByRole('button', { name: 'Open navigation' })
    await expect(burger).toBeVisible()
    // No rail in the flex row at all.
    await expect(page.locator('.gi-sider')).toHaveCount(0)

    const before = await page.locator('.gi-content').boundingBox()
    expect(before!.width).toBeGreaterThan(300)   // content owns the viewport

    await burger.click()
    const drawer = page.locator('.gi-nav-drawer .ant-drawer-body')
    await expect(drawer).toBeVisible()
    await expect(drawer.locator('.ant-menu').first()).toBeVisible()

    // The whole point: opening the nav must not resize the page underneath.
    const after = await page.locator('.gi-content').boundingBox()
    expect(Math.round(after!.width), 'content width must not change')
      .toBe(Math.round(before!.width))

    // Navigating closes it (a drawer left open over the destination is a bug).
    await page.getByRole('menuitem', { name: /stock/i }).first().click()
    await expect(drawer).toBeHidden()
  })

  test('phone: no page-level horizontal scroll on the main tabs', async ({ page }) => {
    await page.setViewportSize(PHONE)
    for (const path of ['/', '/stock', '/hod/approvals']) {
      await page.goto(path)
      await page.waitForTimeout(400)
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow, `${path} must not scroll the page sideways`).toBeLessThanOrEqual(1)
    }
  })

  test('desktop keeps the sticky rail and drops the hamburger', async ({ page }) => {
    await page.setViewportSize(DESKTOP)
    await page.goto('/')
    await expect(page.locator('.gi-sider')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Open navigation' })).toHaveCount(0)
  })

  // ── KPI rows use the whole width (Phase 8 Track 5) ────────────────────────
  // The old pattern was a fixed 4-up slice of a 24-column grid, which fits
  // exactly four cards. Three left a quarter of the row empty and five wrapped
  // one lonely card onto its own line — dead space that reads as a failed load.
  // The assertion is the one a person actually makes looking at the page: the
  // last card in a row reaches the right-hand edge.
  test('desktop: a KPI row leaves no dead space on the right', async ({ page }) => {
    await page.setViewportSize(DESKTOP)
    await page.goto('/')
    const row = page.locator('.gi-kpi-row').first()
    await expect(row).toBeVisible()
    const cells = row.locator('> .gi-kpi-cell')
    expect(await cells.count()).toBeGreaterThan(1)

    const rowBox = (await row.boundingBox())!
    const lastBox = (await cells.last().boundingBox())!
    const trailing = (rowBox.x + rowBox.width) - (lastBox.x + lastBox.width)
    expect(trailing, 'the last KPI card must reach the end of the row')
      .toBeLessThanOrEqual(2)

    // …and every card in the line is the same height, so the baselines agree
    // whatever the title wraps to.
    const heights: number[] = []
    for (let i = 0; i < await cells.count(); i++) {
      heights.push(Math.round((await cells.nth(i).boundingBox())!.height))
    }
    expect(new Set(heights).size, `card heights: ${heights}`).toBe(1)
  })

  test('phone: KPI cards stack full width instead of leaving a ragged grid',
    async ({ page }) => {
      await page.setViewportSize(PHONE)
      await page.goto('/')
      const row = page.locator('.gi-kpi-row').first()
      await expect(row).toBeVisible()
      const rowBox = (await row.boundingBox())!
      const firstBox = (await row.locator('> .gi-kpi-cell').first().boundingBox())!
      // 220px minimum against a 390px phone means one per line, full width.
      expect(Math.round(firstBox.width)).toBeGreaterThan(Math.round(rowBox.width) - 4)
    })
})
