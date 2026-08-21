/**
 * The Manpower Planner's UI state (Phase 8 slice 8b).
 *
 * What this covers that the service suite cannot: the CONTROLS. Suite CF
 * proves the arithmetic of Target Days, two-shift splitting and multi-job
 * resolution; none of that says the Target Days box appears, that switching to
 * "Hours per person" actually swaps the field, or that forcing two shifts is
 * reachable without editing a request by hand.
 *
 * It also pins the one number that is easiest to misread: two shifts SPLITS the
 * crew and does not halve the hiring. The page states that in words, and if
 * somebody ever "simplifies" the wording away this fails.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { storageStatePath } from '../harness/env'

test.use({ storageState: storageStatePath('hod') })
test.describe.configure({ mode: 'serial' })

// The e2e database is the frozen legacy snapshot: real equipment, real SQM
// progress, and NO productivity benchmarks — so a plan over it is all warnings
// and no gap rows. One benchmark, created through the real HOD endpoint, is
// enough to make the per-role dashboard appear, and creating it through the API
// rather than a SQL fixture means the seeding path is itself covered.
const SEED_CODE = '3'          // ARTL30, on equipment 0050 in the snapshot
const SEED_TAG = '0050'

async function seedBenchmark() {
  const api = await apiAs('hod')
  const r = await api.post('/sme/master/manpower-norms', {
    data: {
      Type: 'CV', Lining_System_Code: SEED_CODE,
      Execution_Sub_Activity_Code: 'E2E-ESC', Activity: 'e2e planner activity',
      Sub_Activity: 'e2e detail', Crew_Size: 3, Hours_Per_Shift: 11,
      Manhours_Per_Shift: 330, Standard_Productivity_Per_Shift: 100,
      Crew: { MASON: 2, HELPER: 1 },
    },
  })
  // 409 = a previous run already created it, which is fine and idempotent.
  expect([201, 409]).toContain(r.status())
}

async function openPlanner(page: import('@playwright/test').Page) {
  await page.goto('/manhours')
  await page.getByRole('tab', { name: /Manpower Planner/i }).click()
  await expect(page.getByRole('button', { name: 'Plan' })).toBeVisible()
}

test('the planner opens with a multi-select and a Target Days box', async ({ page }) => {
  await openPlanner(page)

  // Multi-select, not the single-job Select of Phase 7.
  await expect(page.locator('#equipment_tags')).toBeVisible()
  await expect(page.locator('#lining_system_codes')).toBeVisible()
  // Target days is the DEFAULT spelling of the deadline — days is what a
  // planning meeting talks in; hours per person is the specialist form.
  await expect(page.locator('#target_days')).toBeVisible()
  await expect(page.locator('#deadline_hours')).toHaveCount(0)
})

test('the deadline segmented control swaps the field, never shows both', async ({ page }) => {
  await openPlanner(page)

  // antd's Segmented is a label-wrapped radio; clicking the item is what a
  // user does and what survives an antd internals change.
  await page.locator('.ant-segmented-item', { hasText: 'Hours per person' }).click()
  await expect(page.locator('#deadline_hours')).toBeVisible()
  // Both visible at once would let somebody fill in two contradictory
  // deadlines; the API 422s on that pair, and the UI must not offer it.
  await expect(page.locator('#target_days')).toHaveCount(0)

  await page.locator('.ant-segmented-item', { hasText: 'Target days' }).click()
  await expect(page.locator('#target_days')).toBeVisible()
  await expect(page.locator('#deadline_hours')).toHaveCount(0)
})

test('shifts per day is automatic until you take it over', async ({ page }) => {
  await openPlanner(page)

  // Auto by default: the roster decides, and there is nothing to choose.
  await expect(page.locator('.ant-radio-button-wrapper', { hasText: 'Day + Night' })).toHaveCount(0)

  await page.getByRole('switch', { name: /auto/i }).first().click()
  await expect(page.locator('.ant-radio-button-wrapper', { hasText: 'Day only' })).toBeVisible()
  await expect(page.locator('.ant-radio-button-wrapper', { hasText: 'Day + Night' })).toBeVisible()
})

test('"Select all" fills the equipment multi-select in one click', async ({ page }) => {
  await openPlanner(page)

  await page.locator('#equipment_tags').click()
  const selectAll = page.getByRole('button', { name: 'Select all' })
  await expect(selectAll).toBeVisible()
  await selectAll.click()
  // The value is the real option list, never a sentinel — so the tag count in
  // the box is the number of equipment, and every consumer downstream keeps
  // working on a plain array.
  await expect(page.locator('.ant-select-selection-item').first()).toBeVisible()
})

test('a plan renders the days, the per-shift split and the per-role dashboard',
  async ({ page }) => {
    await seedBenchmark()
    await openPlanner(page)

    await page.locator('#equipment_tags').click()
    await page.getByTitle(SEED_TAG, { exact: true }).click()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Plan' }).click()

    await expect(page.getByText('1 · Workload and required hours')).toBeVisible()
    // The three quantities slice 8b added, beside the man-hours that were
    // already there.
    await expect(page.getByText('Crew-shifts of work')).toBeVisible()
    await expect(page.getByText('Days to the deadline')).toBeVisible()
    await expect(page.getByText('Calendar shifts')).toBeVisible()

    await expect(
      page.getByText('2 · What we need, what we have, what to assign')).toBeVisible()
    await expect(page.getByText('Total headcount needed')).toBeVisible()
    await expect(page.getByText('To assign / procure')).toBeVisible()

    // The per-role dashboard is COLLAPSIBLE: the headline is need/have/assign
    // and the detail is one click away rather than nine columns wide.
    const firstRole = page.locator('.ant-collapse-item').first()
    await expect(firstRole).toBeVisible()
    await firstRole.click()
    await expect(page.getByText('Which job asked for this role').first())
      .toBeVisible()
  })

test('a two-shift plan says in words that it splits the crew rather than halving the hiring',
  async ({ page }) => {
    await seedBenchmark()
    await openPlanner(page)

    await page.locator('#equipment_tags').click()
    await page.getByTitle(SEED_TAG, { exact: true }).click()
    await page.keyboard.press('Escape')
    await page.getByRole('switch', { name: /auto/i }).first().click()
    await page.locator('.ant-radio-button-wrapper', { hasText: 'Day + Night' }).click()
    await page.getByRole('button', { name: 'Plan' }).click()

    // THE MISREADING THIS GUARDS: "two shifts, so half the people". Nobody
    // works both shifts, so the total headcount is unchanged and only the
    // per-shift figure halves. If this banner is ever tidied away, a planner
    // reading the smaller number will under-hire by half.
    await expect(page.getByText(
      /Two shifts splits the crew — it does not halve the hiring/)).toBeVisible()
    await expect(page.getByText(/Per shift \(x2\)/)).toBeVisible()
  })
