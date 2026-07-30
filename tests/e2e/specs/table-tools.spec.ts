/**
 * Global table sorting + filtering (frontend/src/lib/smartTable.tsx).
 *
 * Every `<Table>` in the app renders through the smartTable wrapper, so these
 * specs assert the rules it encodes, against real cloned data:
 *   1. client-side grids get a sorter on every field-backed column — and NOT on
 *      computed/action columns, which have nothing to sort by;
 *   2. sorting actually orders the rendered rows, both ways;
 *   3. low-cardinality text columns get a working checkbox filter, while
 *      booleans and numeric measurements get a sorter and no dropdown;
 *   4. server-paginated grids are left alone — sorting one page out of N would
 *      silently lie about every other page.
 */
import { test, expect, Page } from '@playwright/test'
import { storageStatePath } from '../harness/env'

const rowsOf = (page: Page) => page.locator('.ant-table-tbody tr.ant-table-row')
const headersOf = (page: Page) => page.locator('.ant-table-thead th')

/** Visible text of the nth (1-based) body cell of every rendered row. */
async function column(page: Page, n: number): Promise<string[]> {
  return (await page.locator(`.ant-table-tbody tr.ant-table-row td:nth-child(${n})`)
    .allInnerTexts()).map((s) => s.trim())
}

const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' })

test.describe('table-tools:admin', () => {
  test.use({ storageState: storageStatePath('admin') })

  test('every field-backed column sorts, action columns do not', async ({ page }) => {
    await page.goto('/admin/users')
    await expect(rowsOf(page).first()).toBeVisible()
    expect(await rowsOf(page).count()).toBeGreaterThan(2)

    // Username, Role, Site, Warehouse, Phone, 2FA carry a dataIndex; "Actions"
    // is a pure render column and must stay untouched.
    expect(await headersOf(page).count()).toBe(7)
    expect(await page.locator('.ant-table-thead th.ant-table-column-has-sorters').count())
      .toBe(6)
    await expect(headersOf(page).filter({ hasText: 'Actions' }).first())
      .not.toHaveClass(/ant-table-column-has-sorters/)

    const username = headersOf(page).filter({ hasText: 'Username' }).first()
    await username.click()
    const asc = await column(page, 1)
    expect(asc.length).toBeGreaterThan(2)
    expect(asc, 'first click sorts ascending')
      .toEqual([...asc].sort(collator.compare))

    await username.click()
    expect(await column(page, 1), 'second click reverses it').toEqual([...asc].reverse())
  })

  test('text columns filter; booleans and action columns do not', async ({ page }) => {
    await page.goto('/admin/users')
    await expect(rowsOf(page).first()).toBeVisible()
    const before = await rowsOf(page).count()

    // 2FA is a boolean — sortable, but no "true/false" dropdown.
    const twofa = headersOf(page).filter({ hasText: '2FA' }).first()
    await expect(twofa).toHaveClass(/ant-table-column-has-sorters/)
    await expect(twofa.locator('.ant-table-filter-trigger')).toHaveCount(0)
    await expect(headersOf(page).filter({ hasText: 'Actions' }).first()
      .locator('.ant-table-filter-trigger')).toHaveCount(0)

    // Role holds a handful of distinct strings → checkbox filter.
    const role = headersOf(page).filter({ hasText: 'Role' }).first()
    const trigger = role.locator('.ant-table-filter-trigger')
    await expect(trigger).toBeVisible()

    await trigger.click()
    const dropdown = page.locator('.ant-table-filter-dropdown').last()
    await expect(dropdown).toBeVisible()
    await dropdown.locator('.ant-dropdown-menu-item').first().click()
    await dropdown.getByRole('button', { name: /^ok$/i }).click()

    await expect(rowsOf(page).first()).toBeVisible()
    const after = await rowsOf(page).count()
    expect(after, 'filtering must remove rows').toBeLessThan(before)
    // Whatever the Role cell renders, every surviving row must render the same.
    const roles = await column(page, 2)
    expect(new Set(roles).size, 'all surviving rows share one role').toBe(1)
  })

  test('server-paginated audit log opts out of client-side sorting', async ({ page }) => {
    await page.goto('/admin/audit')
    await expect(rowsOf(page).first()).toBeVisible()
    // Controlled `current` + `total` → the server owns paging, so smartTable
    // must not attach sorters it could only apply to the visible page.
    await expect(page.locator('.ant-table-thead th.ant-table-column-has-sorters'))
      .toHaveCount(0)
  })
})
