/**
 * Man-hours per m² over time (Phase 9e).
 *
 * Suite CO proves the arithmetic — the two divisions by zero, the cumulative
 * figure, the reason passthrough. What it cannot prove is that the chart DRAWS
 * the gap. A line that quietly bridged a zero-area day would show a job
 * running steadily through a fortnight of scaffolding, and the number it drew
 * there would not exist.
 */
import { test, expect } from '@playwright/test'
import { apiAs } from '../harness/api'
import { storageStatePath } from '../harness/env'

test.use({ storageState: storageStatePath('hod') })
test.describe.configure({ mode: 'serial' })

// day, tag, hours, m², remark.  T1 scaffolds for two days then works;
// T2 just works. 396h/60m² = 6.6 against 66h/125m² = 0.53.
const ROWS: Array<[string, string, number, number, string]> = [
  ['2026-08-01', 'E2EF-T1', 88, 0, 'Scaffolding erection'],
  ['2026-08-02', 'E2EF-T1', 88, 0, 'Scaffolding erection'],
  ['2026-08-03', 'E2EF-T1', 88, 40, ''],
  ['2026-08-05', 'E2EF-T1', 44, 20, ''],
  ['2026-08-06', 'E2EF-T1', 88, 0, ''],
  ['2026-08-03', 'E2EF-T2', 22, 40, ''],
  ['2026-08-05', 'E2EF-T2', 22, 40, ''],
  ['2026-08-06', 'E2EF-T2', 22, 45, ''],
]

// ⚠️ THE EMPLOYEES THIS SEEDS FEED THE PLANNER'S ROSTER. Eight masons left
// active would change `roster.In_Scope`, `Days_With_Current_Roster` and the
// hire advice for every planner spec that runs after this one — the same class
// of cross-spec interference that made an unrelated suite fail intermittently
// in 9d. They are deactivated in afterAll; `planner.roster()` counts only
// active rows, so that is what actually removes them from the arithmetic.
const SEEDED_EMPLOYEES = new Set<string>()

test.beforeAll(async () => {
  const api = await apiAs('hod')
  for (const [d, tag, h, sq, rem] of ROWS) {
    const codes: string[] = []
    for (let i = 0; i < Math.round(h / 11); i += 1) {
      const c = `E2EF-${tag}-${i}`
      codes.push(c)
      await api.post('/mh/employees', {
        data: { employee_code: c, name: `e2e ${i}`, designation: 'Mason' } })
      SEEDED_EMPLOYEES.add(c)
    }
    if (codes.length) {
      await api.post('/mh/timesheets', {
        data: {
          work_date: d, equipment_tag: tag, system_code: 'E2EF-SYS',
          break_mins: 0,
          rows: codes.map((c) => ({ employee_code: c, in_time: '07:00',
                                    out_time: '18:00', remarks: rem })),
        } })
    }
    if (sq) {
      await api.post('/mh/production', {
        data: { work_date: d, equipment_tag: tag, system_code: 'E2EF-SYS',
                sqm_done: sq } })
    }
  }
  await api.dispose()
})

test.afterAll(async () => {
  const api = await apiAs('hod')
  const roster = await (await api.get('/mh/employees')).json()
  for (const r of (roster.items ?? []) as Array<{ id: number; Employee_Code: string }>) {
    if (SEEDED_EMPLOYEES.has(r.Employee_Code)) {
      await api.patch(`/mh/employees/${r.id}/status?status=inactive`).catch(() => {})
    }
  }
  await api.dispose()
})

test('the tab opens by URL and leads with the comparison, not the chart',
  async ({ page }) => {
    await page.goto('/manhours?tab=efficiency')
    await expect(page.getByText('Day by day')).toBeVisible({ timeout: 30_000 })
    // ⚠️ THE ANSWER BEFORE THE PICTURE. 0.53 against 6.60 is the operator's
    // actual question — which tank cost more manpower per metre — and it is
    // readable without interpreting a single bar.
    // The figure appears twice — the KPI card and the Totals panel — so scope
    // to the cards, which are what a reader sees first.
    await expect(page.locator('.gi-kpi').filter({ hasText: '6.60' })).toBeVisible()
    await expect(page.locator('.gi-kpi').filter({ hasText: '0.53' })).toBeVisible()
  })

test('a day with hours and no area is called out, with what the timekeeper wrote',
  async ({ page }) => {
    await page.goto('/manhours?tab=efficiency')
    await expect(page.getByText('Days with hours but no area'))
      .toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Scaffolding erection').first()).toBeVisible()
    // ⚠️ AND WHERE NOTHING WAS WRITTEN, IT SAYS THAT. Filling this with
    // "mobilisation" would put a word in somebody's mouth; "no reason
    // recorded" is a thing a manager can act on.
    await expect(page.getByText(/no reason recorded/)).toBeVisible()
  })

test('the warning names the unexplained day rather than leaving it to be noticed',
  async ({ page }) => {
    await page.goto('/manhours?tab=efficiency')
    await expect(page.getByText(/booked hours with no area and no note/))
      .toBeVisible({ timeout: 30_000 })
  })

test('the chart says out loud that the line is the RUNNING figure',
  async ({ page }) => {
    await page.goto('/manhours?tab=efficiency')
    // The single most misreadable thing here is taking the line for a daily
    // rate. It is cumulative, and the page has to say so in words — a reader
    // who thinks 6.6 was one day's performance draws the wrong conclusion.
    await expect(page.getByText(/the line is the job/)).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/does not exist at all on\s+a day with no area/))
      .toBeVisible()
  })
