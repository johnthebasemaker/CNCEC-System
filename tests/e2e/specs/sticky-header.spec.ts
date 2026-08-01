/**
 * Sticky table headers must freeze ABOVE the rows, never over them.
 *
 * Two defects produced the portrait-mode overlap, and both are pinned here:
 *
 *   1. `scroll.y` + `sticky` are mutually exclusive. With a body height antd
 *      already pins the header inside the table's own scroll box; adding
 *      viewport-sticky detached that header and floated it into the MIDDLE of
 *      the scrolling body, so rows stayed visible above it and slid under it.
 *      smartTable now drops `sticky` whenever `scroll.y` is set.
 *   2. antd computed the sticky holder's z-index per stacking context and it
 *      came out inconsistently (measured 9 on one Dashboard table, 23 on the
 *      next). Anything above the app header's z-index 20 makes a table header
 *      ride OVER the app chrome.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

const PORTRAIT = { width: 820, height: 1180 }   // tablet portrait — the report
const PHONE = { width: 390, height: 844 }

test.describe('sticky-header:admin', () => {
  test.use({ storageState: storageStatePath('admin') })

  test('portrait: a scroll.y table pins its header without a floating overlay',
    async ({ page }) => {
      await page.setViewportSize(PORTRAIT)
      await page.goto('/')
      const card = page.locator('.ant-card').filter({ hasText: 'Inventory by category' })
      // `tr.ant-table-row` skips antd's hidden measure row.
      await expect(card.locator('.ant-table-tbody tr.ant-table-row').first()).toBeVisible()

      // The table owns a 320px scroll box, so there must be NO viewport-sticky
      // holder competing with it.
      await expect(card.locator('.ant-table-sticky-holder')).toHaveCount(0)

      await page.evaluate(() => window.scrollTo(0, 900))
      await page.waitForTimeout(300)

      // The header sits exactly on top of the body: no gap, no overlap.
      const geom = await card.evaluate((el) => {
        const head = el.querySelector('.ant-table-header') as HTMLElement
        const body = el.querySelector('.ant-table-body') as HTMLElement
        body.scrollTop = 160
        return {
          headBottom: head.getBoundingClientRect().bottom,
          bodyTop: body.getBoundingClientRect().top,
          scrolled: body.scrollTop,
        }
      })
      expect(geom.scrolled, 'the body really scrolled').toBeGreaterThan(0)
      expect(Math.abs(geom.headBottom - geom.bodyTop),
        'header must butt against the body, not overlap it').toBeLessThanOrEqual(1)
    })

  test('a viewport-sticky header stacks under the app header, never over it',
    async ({ page }) => {
      await page.setViewportSize(PORTRAIT)
      await page.goto('/')
      const holder = page.locator('.ant-table-sticky-holder').first()
      await expect(holder).toBeAttached()

      const z = await holder.evaluate((el) => Number(getComputedStyle(el).zIndex))
      const appZ = await page.locator('.gi-header')
        .evaluate((el) => Number(getComputedStyle(el).zIndex))
      expect(z, 'table header must not ride over the app chrome').toBeLessThan(appZ)

      // Opaque, or the rows travelling underneath show through the header.
      const bg = await holder.evaluate((el) => getComputedStyle(el).backgroundColor)
      expect(bg, 'sticky holder must be opaque').toMatch(/^rgb\(/)
      expect(bg).not.toMatch(/rgba\([^)]*,\s*0(\.\d+)?\)$/)
    })

  test('the sticky offset tracks the real app header height at each breakpoint',
    async ({ page }) => {
      for (const size of [PORTRAIT, PHONE]) {
        await page.setViewportSize(size)
        await page.goto('/')
        await page.waitForTimeout(250)
        const { varH, realH } = await page.evaluate(() => ({
          varH: parseInt(getComputedStyle(document.documentElement)
            .getPropertyValue('--gi-header-h'), 10),
          realH: Math.round(document.querySelector('.gi-header')!.getBoundingClientRect().height),
        }))
        // A mismatch is exactly the gap rows used to show through.
        expect(varH, `--gi-header-h must equal the header at ${size.width}px`).toBe(realH)
      }
    })
})
