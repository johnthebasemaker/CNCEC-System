/**
 * Server configuration on the login screen (native-app environment routing).
 *
 * The installed APK/EXE/DMG is built once against production, so the ONLY way
 * to aim it at a dev tunnel is a runtime override. This pins the contract the
 * axios client depends on: the choice persists in localStorage under
 * `gi_api_base`, a bare host is normalised to https://<host>/api, and clearing
 * it falls back to the build-time default.
 */
import { test, expect } from '@playwright/test'

test.describe('server-config', () => {
  // The login screen is pre-auth by definition.
  test.use({ storageState: { cookies: [], origins: [] } })

  test('the gear stores a normalised override and can reset it', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('button', { name: 'Server configuration' })).toBeVisible()
    await page.getByRole('button', { name: 'Server configuration' }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    // A bare host is what a tester actually types.
    await dialog.getByPlaceholder('/api').fill('local.giinventory.com')
    await expect(dialog.getByText('Will connect to https://local.giinventory.com/api')).toBeVisible()

    await dialog.getByRole('button', { name: /save & reconnect/i }).click()
    await expect.poll(async () =>
      page.evaluate(() => localStorage.getItem('gi_api_base')),
    ).toBe('https://local.giinventory.com/api')

    // Switching servers must end the session — a token from one server is
    // meaningless to another.
    expect(await page.evaluate(() => localStorage.getItem('gi_token'))).toBeNull()

    // …and the login screen says where it is pointing, so nobody debugs a
    // "wrong password" that is really a wrong server.
    await page.waitForLoadState('domcontentloaded')
    await expect(page.getByText('Connected to https://local.giinventory.com/api')).toBeVisible()

    // Reset returns to the build default.
    await page.getByRole('button', { name: 'Server configuration' }).click()
    await page.getByRole('dialog').getByRole('button', { name: /reset to default/i }).click()
    await expect.poll(async () =>
      page.evaluate(() => localStorage.getItem('gi_api_base')),
    ).toBeNull()
  })
})
