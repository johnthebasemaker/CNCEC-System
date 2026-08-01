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
})
