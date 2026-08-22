/**
 * Shared UI helpers for specs.
 */
import { expect, Page } from '@playwright/test'

/**
 * Open a tab BY NAME, whether it is on the bar or past the end of it.
 *
 * ⚠️ A REACHABLE TAB IS A COINCIDENCE OF WIDTH. antd keeps every tab in the DOM
 * and simply lets the ones that do not fit run off the end of an
 * `overflow: hidden` strip — `visibility: visible`, real coordinates, and at
 * x=1842 on a 1280px viewport. Playwright cannot click that, and rc-tabs scrolls
 * the strip with a transform rather than `scrollLeft`, so no amount of
 * scrolling-into-view helps. Labor Tracking has twelve tabs and only about six
 * of them are on screen; which six depends on the label text.
 *
 * The overflow menu is the reachable route, and its items are
 * `role="option"` in a listbox — NOT `menuitem`. That detail cost an hour: the
 * dropdown opened, the menuitem query matched nothing, and the failure read as
 * "the tab does not exist".
 *
 * Written against BEHAVIOUR: try the tab; if it did not become selected, go
 * through the menu. Prefer a `?tab=` URL where the page supports one — it is
 * deterministic and does not depend on any of this.
 */
export async function openTab(page: Page, name: RegExp | string) {
  const tab = page.getByRole('tab', { name }).first()
  await expect(tab).toBeAttached()

  const selected = async () => (await tab.getAttribute('aria-selected')) === 'true'
  if (!(await selected())) {
    await tab.click({ timeout: 3000 }).catch(() => { /* past the end of the bar */ })
  }
  if (!(await selected())) {
    const more = page.locator('.ant-tabs-nav-more').first()
    await expect(more).toBeVisible()
    await more.click()
    await page.getByRole('option', { name }).first().click()
  }
  await expect(tab).toHaveAttribute('aria-selected', 'true')
}
