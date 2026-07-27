# Phase 2 · Theme C — run log

**Findings closed:** `A03-F3` (High) · `A03-F5` · `A03-F6` · `A03-F8` · `A03-F9`
(Medium) · `A03-F11` · `A02-F9` · `A02-F11` (Low)
**Already closed before this branch:** `A03-F4`
**Date:** 2026-07-27 · **Branch:** `security/phase-2-theme-c-auth-surface`

---

## 1. Findings and fixes

### `A03-F4` — already closed, verified not re-fixed
The audit reported `auth.py` computing `production` from its own inline
`GI_ENV == "production"` check while `config.is_production()` accepts
`prod|production`. `_set_refresh_cookie` **already** calls the canonical
`is_production()` (`auth.py:165`, imported at line 34) — the operator's
pre-Phase-2 `A03-F1` work closed it. No change made; recorded so the finding
isn't left looking open.

### `A03-F3` — 2FA brute force
`/2fa/enroll`, `/2fa/verify` and `/2fa/disable` carried no rate limit at all,
while every other auth route did. `_verify_totp` runs `valid_window=1`, so
three 6-digit codes are acceptable at any instant — with unlimited guesses, a
stolen access token could strip 2FA permanently.

Added `rate_limit(5, 60)` to all three **plus** a per-**username** failure
budget (`_TOTP_MAX_ATTEMPTS = 5` / 15 min). The per-IP limit alone is
defeatable by rotating `CF-Connecting-IP` (`A03-F6`); a username is not
header-controllable, so that is the ceiling that actually holds. Only failures
count; a correct code clears the record. Failed disables are audited.

### `A03-F8` — 2FA enrollment step-up
A bearer token alone could bind a **new** authenticator, converting a stolen
15-minute token into durable persistence. `/2fa/enroll` now takes a `password`
body and verifies it with `_verify_password` before writing a secret; refusals
are audited and write no secret.

### `A03-F9` — stale authorization
`admin.update_user` never revoked sessions, so a demoted or re-pinned user kept
their old role/site/warehouse for up to 15 minutes (the claims ride inside the
access token and are read back with no DB lookup). Now calls
`revoke_all_sessions(..., "authz-changed")` whenever `role`, `Site_ID` or
`Warehouse_ID` actually changes — compared against the loaded row, so a no-op
PATCH doesn't log everyone out.

### `A03-F5` — production CORS
`docker-compose.prod.yml` passes `CORS_ORIGINS` as an **empty string** when
unset; `.strip()` made it falsy and the dev list applied in production, making
`http://localhost{,:3000,:5173}` credentialed origins against the live API.
Production now defaults to `[]` (no CORS needed behind the single-origin nginx
proxy) and must opt in explicitly; dev is unchanged.

### `A03-F6` — spoofable rate-limit keys
`_client_ip` trusted `CF-Connecting-IP`/`X-Real-IP` from any peer, so rotating
one header gave a fresh bucket per request. Now gated behind
`GI_TRUSTED_PROXIES`. **Default is empty = previous behaviour**, deliberately:
the tunnel/nginx peer address differs per deployment, and defaulting to "trust
nothing" would key every user on one IP and cause its own outage. The deploy
box sets the variable. Firewalling the origin to Cloudflare ranges remains an
operator task (audit §6).

### `A03-F11` — password floor
`MIN_PW` 6 → **12**, and registration (which carried its own literal `6`) now
imports the same constant — the weakest door previously set the real floor.
**Login has no length check, so existing shorter passwords still
authenticate**; the policy binds new and reset credentials only. Frontend hints
updated to match on the create/reset forms.

### `A02-F9` — unknown-role fail-open
`warehouse_scope` returned `None` (unrestricted) for any role string that
wasn't literally `warehouse_user`. With `_public()`'s unknown-role fallback
(level 0), a typo in `users.role` produced the worst pair: lowest privilege on
the ladder **and** global warehouse visibility. Unknown roles now return `""`
(matches nothing); the real roles are unchanged.

### `A02-F11` — `/health` disclosure
Anonymous `/health` returned the database name, driver dialect, maintenance
state and the full entity inventory — and becomes internet-reachable once the
Cloudflare Access bypass for `/api/*` lands. Reduced to
`{status, maintenance}`; diagnostics moved to **`/health/detail`** behind
`require_level(4)`, which additionally surfaces `ai_readonly_wall` (Theme B).

## 2. Frontend follow-through — a caught breaking change

Reducing `/health` broke the UI, which the backend gates could not see:
`AppLayout.tsx:201` rendered `` `${health.dialect} · ${health.database}` `` and
`Dashboard.tsx:73` rendered `health.dialect`. Left as-is these would have
printed `undefined · undefined` to every user. Both now render a plain
online/offline status, and the `Health` TypeScript interface was narrowed to
match the real payload.

A second near-miss: the `MIN_PW` bump was initially applied to the **sign-in**
form's validation rule as well, which would have locked out every existing user
whose password is shorter than 12. Reverted — the sign-in form enforces
`required` only, with a comment explaining why.

## 3. Test evidence — suite AT (15 checks)

Unknown-role fail-closed + role-ladder non-regression · forwarded-header
trust matrix (untrusted peer / trusted peer / unconfigured) · production vs dev
CORS defaults via module reload · anonymous `/health` field absence and
`/health/detail` 401/403/200 matrix · enroll step-up (missing password 422,
wrong password 403, **no secret written**, correct password 200) · TOTP guess
budget asserted as a property (bounded, never succeeds) rather than an exact
index, since two limiters overlap · the 12-char floor on both registration and
admin create **plus** an explicit check that existing short passwords still
authenticate · session revocation on a site change.

## 4. Gates

| Gate | Before | After |
|---|---|---|
| `backend.api.service_tests` | 809 / 0 | **824 / 0** (+15) |
| Playwright E2E | 39 / 39 | **39 / 39** |
| `legacy/bug_check.py` | 599 / 0 | **599 / 0** |
| `npm run build --prefix frontend` + `tsc --noEmit` | ✅ | ✅ |

One Playwright run showed `offline-queue.spec.ts` failing; it passed in
isolation and on two subsequent full runs, and the spec is timing/network
sensitive. Recorded as a flake, not a regression — worth watching.

`gi_database.db` sha256 verified identical (`shasum -c: OK`); never staged.

## 5. Operator action required

- Set **`GI_TRUSTED_PROXIES`** on the deploy box to the tunnel/nginx peer
  address, otherwise the rate limiter keeps trusting forwarded headers.
- Set **`CORS_ORIGINS`** explicitly in production if the native shells call the
  API cross-origin — the dev fallback no longer applies there.
- Existing accounts keep their current passwords; the 12-char floor applies at
  the next reset.
