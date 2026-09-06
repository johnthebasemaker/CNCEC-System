/**
 * sample_tutorial.spec.ts — PHASE 12 PROTOTYPE.
 *
 * One tutorial, recorded end to end: a Store Keeper signs in to the isolated
 * E2E stack, opens the Hub Assistant, asks it a question, and reads the answer.
 * The output is a `.webm` plus a `beats.json` naming the millisecond each step
 * completed; `tools/generate_tutorial.py` composites the two into an MP4.
 *
 * ⚠️ THIS IS NOT A TEST AND MUST NEVER BECOME A GATE. It asserts only enough to
 * fail loudly on a recording that would be silently wrong — a panel that never
 * opened, an answer that never rendered. A tutorial that renders badly is a
 * tutorial to re-render, not a red build. It is excluded from
 * `tests/e2e/playwright.config.ts` by living under its own config.
 *
 * Run: `.venv/bin/python tools/generate_tutorial.py`
 */
import { test, expect } from '@playwright/test'
import * as path from 'node:path'
import { type Role, storageStatePath } from '../e2e/harness/env'
import {
  Beats, RENDER, VIDEO, glideClick, installTutorialChrome, loadShotList,
  newRecordingContext, outDir, scriptAssistant, trackNavigation,
} from './harness/record'

const shot = loadShotList()

// Serial and alone. Recording is wall-clock work: a parallel worker stealing
// CPU shows up as dropped frames, which is a defect you can only see by
// watching the file.
test.describe.configure({ mode: 'serial' })

test(`record ${shot.tutorial_id}`, async ({ browser }) => {
  const dir = outDir()
  const raw = path.join(dir, 'raw')

  const context = await newRecordingContext(browser, storageStatePath(shot.role as Role), raw)
  await installTutorialChrome(context, shot)
  const beats = new Beats()
  const page = await context.newPage()
  trackNavigation(page, beats)
  await scriptAssistant(page, shot)

  // ── 1. the dashboard ─────────────────────────────────────────────────────
  await page.goto('/')
  const fab = page.getByRole('button', { name: 'Open Hub Assistant' })
  await expect(fab).toBeVisible()
  // Park the pointer somewhere neutral so the synthetic cursor exists on the
  // first frame instead of appearing from nowhere at the first click.
  await page.mouse.move(RENDER.css.width / 2, RENDER.css.height / 2)
  await page.waitForTimeout(400)
  beats.mark('title', 'Store Keeper dashboard, signed in')
  await beats.hold(page, shot, 'title', 2600)

  // ── 2. open the assistant ────────────────────────────────────────────────
  await glideClick(page, '[aria-label="Open Hub Assistant"]')
  const panel = page.locator('.ant-card', { hasText: 'Hub Assistant' })
  await expect(panel).toBeVisible()
  beats.mark('open_assistant', 'The assistant opens from any page')
  await beats.hold(page, shot, 'open_assistant', 2200)

  // ── 3. type the question ─────────────────────────────────────────────────
  const input = panel.getByPlaceholder('Ask the manual…')
  await expect(input).toBeEnabled()
  await glideClick(page, '.ant-card input[placeholder="Ask the manual…"]')
  // Typed a character at a time on purpose: a field that fills instantly reads
  // as a screenshot, and the viewer stops believing the recording is the app.
  await input.pressSequentially(shot.assistant_question, { delay: 55 })
  beats.mark('question', shot.assistant_question)
  await beats.hold(page, shot, 'question', 700)

  // ── 4. send, and let "Thinking…" be seen ─────────────────────────────────
  await glideClick(page, '[aria-label="Send"]')
  await expect(panel.getByText('Thinking…')).toBeVisible()
  beats.mark('thinking', 'The question goes to the LOCAL model')

  // ── 5. the answer ────────────────────────────────────────────────────────
  const firstWords = shot.assistant_answer.trim().split(/\s+/).slice(0, 4).join(' ')
  await expect(panel.getByText(new RegExp(escapeRe(firstWords)))).toBeVisible()
  beats.mark('answer', 'Answered from the Store Keeper chapters only')
  await beats.hold(page, shot, 'answer', 5200)

  // ── 6. close ─────────────────────────────────────────────────────────────
  await glideClick(page, '.ant-card [aria-label="Close"]')
  await expect(panel).toBeHidden()
  beats.mark('close', 'Back to work')
  await beats.hold(page, shot, 'close', 1800)

  // ── 7. hand the artefacts to the compositor ──────────────────────────────
  // Grab the Video handle BEFORE the context closes — the reference stays
  // valid, `page.video()` afterwards does not.
  const video = page.video()
  if (!video) throw new Error('recordVideo produced no video handle')
  const total = beats.elapsedMs
  await context.close()

  const dest = path.join(dir, `${shot.tutorial_id}.webm`)
  await video.saveAs(dest)
  beats.write(path.join(dir, 'beats.json'), {
    tutorial_id: shot.tutorial_id,
    role: shot.role,
    language: shot.language,
    video: dest,
    size: VIDEO,
    css_viewport: RENDER.css,
    device_scale_factor: RENDER.dpr,
    approx_duration_ms: total,
  })
  console.log(`[video] ${dest}`)
})

function escapeRe(s: string): string { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') }
