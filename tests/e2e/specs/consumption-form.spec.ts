/**
 * The printed consumption form, in a real browser (Phase 9c).
 *
 * Suite CM proves the PDF, the QR and the registry. What it cannot prove is
 * that a supervisor — the person who actually carries this paper into the
 * plant — can reach it. The endpoint deliberately does NOT live under `/mh`,
 * which is exact-locked to {hod, admin}; putting the download there would have
 * handed the form to everybody except its user.
 */
import { test, expect } from '@playwright/test'
import { storageStatePath } from '../harness/env'

test.describe.configure({ mode: 'serial' })

for (const role of ['supervisor', 'hod', 'sk'] as const) {
  test(`a ${role} can reach the print card on the execution page`,
    async ({ browser }) => {
      const ctx = await browser.newContext({ storageState: storageStatePath(role) })
      const page = await ctx.newPage()
      await page.goto('/execution')
      await expect(page.getByText('Print a consumption form')).toBeVisible()
      // The rule that is least obvious and most likely to be "tidied away".
      await expect(page.getByText(/Each download is a separate numbered sheet/))
        .toBeVisible()
      await ctx.close()
    })
}

test('picking a system downloads a PDF, and a second download is a different sheet',
  async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: storageStatePath('supervisor') })
    const page = await ctx.newPage()
    await page.goto('/execution')

    // antd renders a Select's placeholder as a span, not an input attribute, so
    // scope to the card and take its first combobox.
    const card = page.locator('.ant-card', { hasText: 'Print a consumption form' })
    await card.getByRole('combobox').first().click()
    const first = page.locator('.ant-select-item-option').first()
    await expect(first).toBeVisible()
    await first.click()

    const uuids: string[] = []
    for (let i = 0; i < 2; i += 1) {
      const [dl] = await Promise.all([
        page.waitForEvent('download'),
        card.getByRole('button', { name: 'Download' }).click(),
      ])
      const name = dl.suggestedFilename()
      expect(name).toMatch(/^consumption-.*\.pdf$/)
      uuids.push(name)
    }
    // ⚠️ TWO PRINTS ARE TWO SHEETS. The filename carries the Form_UUID, so two
    // identical names would mean the upload side cannot tell a re-print from a
    // re-photograph — which is the whole basis of duplicate detection in 9d.
    expect(uuids[0]).not.toEqual(uuids[1])
    await ctx.close()
  })
