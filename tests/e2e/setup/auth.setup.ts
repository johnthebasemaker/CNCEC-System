/**
 * Mints one Playwright storageState per role by logging in through the API
 * (not the UI) and planting the access token in localStorage — the SPA's
 * AuthContext hydrates it via /auth/me on first load. UI login itself is
 * covered separately by specs/auth.spec.ts.
 */
import { test as setup, expect } from '@playwright/test'
import * as fs from 'node:fs'
import {
  API_URL, E2E_PASSWORD, Role, USERS, WEB_URL, apiHeaders, storageStatePath,
} from '../harness/env'

setup('mint per-role storage states', async ({ request }) => {
  // ⚠️ ONE IP BUCKET PER ROLE, not one for the loop. Every login shared bucket
  // '250', and the rate limiter is per bucket — so the list simply had to grow
  // long enough (it hit the limit at eleven) for setup to start failing with a
  // 429, which reads as "the new account is broken" rather than "the fixture
  // out-grew its own bucket". A distinct bucket per role is also closer to the
  // truth: these are different people on different machines.
  const roles = Object.entries(USERS) as [Role, string][]
  for (const [i, [role, username]] of roles.entries()) {
    const r = await request.post(`${API_URL}/auth/login`, {
      headers: apiHeaders(String(200 + i)),
      data: { username, password: E2E_PASSWORD },
    })
    expect(r.status(), `login ${username} (${role})`).toBe(200)
    const { access_token } = (await r.json()) as { access_token: string }
    expect(access_token, `token for ${username}`).toBeTruthy()

    const state = {
      cookies: [],
      origins: [
        {
          origin: WEB_URL,
          localStorage: [{ name: 'gi_token', value: access_token }],
        },
      ],
    }
    fs.writeFileSync(storageStatePath(role), JSON.stringify(state, null, 2))
  }
})
