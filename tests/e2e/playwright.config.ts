import { defineConfig, devices } from '@playwright/test'
import { WEB_URL } from './harness/env'

/**
 * GI Hub headless E2E. The whole stack (throwaway DB + hermetic backend +
 * Vite) is built by global-setup and torn down by global-teardown; the `setup`
 * project then mints one storageState per role so specs never log in through
 * the UI (except auth.spec.ts, which tests the login form itself).
 */
export default defineConfig({
  testDir: '.',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: '.report' }]],
  outputDir: '.results',
  globalSetup: './global-setup',
  globalTeardown: './global-teardown',
  use: {
    baseURL: WEB_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'setup', testMatch: /setup\/auth\.setup\.ts/ },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      dependencies: ['setup'],
      testMatch: /specs\/.*\.spec\.ts/,
      testIgnore: /specs\/(entry-docs|wbs-work-types)\.spec\.ts/,
    },
    {
      // entry-docs flips the GLOBAL require_entry_documents setting — run it
      // strictly AFTER the parallel pack so the flip can't 422 other specs.
      name: 'gated',
      use: { ...devices['Desktop Chrome'] },
      dependencies: ['chromium'],
      testMatch: /specs\/entry-docs\.spec\.ts/,
    },
    {
      // ⚠️ SAME HAZARD, ONE LEVEL WORSE. Adding the first WBS number for a site
      // turns `assert_wbs` ON for that site, and adding the first work type
      // turns the strict dropdown on — both are conditional gates whose whole
      // design is that they do nothing until an HOD curates a list. A scoped
      // HOD can only manage their OWN site, which is the site every other spec
      // posts entries to, so this cannot be isolated by choosing a different
      // one. It therefore runs strictly LAST, after `gated`, and closes what it
      // opened. Run inside the parallel pack it turns the gate on mid-flight
      // and 422s whichever specs happen to be posting at that moment — which is
      // exactly what it did, in a different spec each run.
      name: 'site-config',
      use: { ...devices['Desktop Chrome'] },
      dependencies: ['gated'],
      testMatch: /specs\/wbs-work-types\.spec\.ts/,
    },
  ],
})
