# Security Suggestions — Forward Hardening Roadmap

**Date:** 2026-08-06
**Author:** Claude Code
**Basis:** Full-codebase review (see `SECURITY_REVIEW_2026-08-05.md`) plus the follow-up work on branches `fix/formula-injection-and-csp` (merged, PR #32) and `fix/sme-export-and-nginx-headers`.
**Status:** Recommendations only. Nothing here is a known-exploitable vulnerability — the audit found one Medium issue and it is fixed. This is the *next* layer.

---

## Where the project already stands

Worth stating plainly, because it changes what is worth doing next: this codebase is **well above** the median for an internal line-of-business application. The audit found no High-severity issues, no SQL injection, no authentication bypass, no XSS sink, and no unsafe deserialization. Several defenses are better than what most production systems ship:

- Refresh-token rotation with **family revocation on replay** — theft detection, not just expiry.
- Auditor read-only enforced as **method-based middleware with an allowlist**, so a new `@router.post` fails closed by default.
- Site and warehouse scoping that **fails closed on `''`** and on unknown role strings.
- A production **fail-fast guard that rejects published placeholder secrets**, not just short ones.
- **Two independent walls** on the AI NL→SQL lane — a text gate *and* a real read-only Postgres role.
- **Step-up password re-entry** to enroll a new 2FA authenticator.

So the recommendations below are about **residual risk, operational maturity, and defense in depth** — not about fixing something broken. They are ordered by value-for-effort, not by severity.

---

## Tier 1 — Do next (high value, low-to-moderate effort)

### 1.1 Move the rate limiters to a shared store

**What:** Back `ratelimit.py` with Redis (or a Postgres table, following the existing `assert_login_allowed_shared` pattern) instead of per-process dictionaries.

**Why:** `deploy/Dockerfile.api:48` runs `uvicorn --workers 4`. Every limiter in `ratelimit.py` except the shared login budget is a per-process `defaultdict` (`ratelimit.py:31`, `:132`, `:185`). With four workers round-robining requests, the **real** limits are roughly 4× what the code says:

| Declared | Actual across 4 workers |
|---|---|
| `/auth/login` 10/60s per IP | ~40/60s |
| `/auth/phone/request-otp` 3/hour per number | ~12/hour |
| 2FA guess budget 5 per 15 min | ~20 per 15 min |
| Webhook HMAC penalty box, 5 strikes | ~20 strikes |

The module's own header comment already says this ("for a hard cross-worker limit use a shared store (e.g. Redis)... documented Phase 3 fix"). The login path was correctly hardened this way already — the pattern exists, it just needs extending to the other four.

The OTP toll-fraud guard is the one I would prioritise: it is the limiter with a direct **financial** cost per bypass.

**Effort:** ~1 day. Redis is a new dependency and a new container; the Postgres-table route reuses `assert_login_allowed_shared` and adds no infrastructure, at the cost of a write per request on hot paths.

**Recommendation:** Postgres table for the OTP and 2FA budgets (low request volume, no new infra). Redis only if you later want per-IP limits on high-traffic read endpoints.

### 1.2 Add CSRF protection to `/auth/refresh`

**What:** A double-submit token, or an `X-Requested-With`-style custom-header requirement, on the refresh endpoint.

**Why:** In production the refresh cookie is `SameSite=none; Secure` (`auth.py:189`) — required, because the Tauri/Capacitor shells call the API cross-site and would otherwise lose silent refresh. The existing comment argues the exposure is contained because CORS keeps the *response body* unreadable to a non-allowed origin, and that is correct as far as it goes.

But the **side effect still fires**. Any page on the internet can make the browser POST to `/auth/refresh` with the victim's cookie attached. It cannot read the new access token, but it *does* rotate the family: the victim's own next refresh now presents a superseded token, which `auth.py:552` correctly treats as replay evidence and **revokes the entire family**. A third-party page can therefore force-logout any logged-in user, repeatedly, without ever seeing a token.

That is a nuisance rather than a breach, and it is genuinely mitigated by the design working as intended. But the fix is small and it removes a whole class of cross-site interaction.

**Effort:** ~2 hours. Requiring a custom header (e.g. `X-GI-Client`) is the cheapest form — browsers will not send it cross-origin without a preflight that CORS then refuses. The native shells already control their own request headers.

**Care:** Verify the Tauri and Capacitor shells send the header before shipping, or silent refresh breaks on native. Suite AQ (`test_rtr`) should cover both the header-present and header-absent cases.

### 1.3 Enforce 2FA for `admin` and `logistics`

**What:** Make TOTP mandatory for the two unscoped roles (level ≥ 3). Existing accounts get a grace period, then a forced enrolment interstitial at login.

**Why:** 2FA is fully implemented and correctly hardened — including the step-up check at `auth.py:779` that most implementations miss — but it is **entirely opt-in** (`auth.py:486` only challenges when `totp_enabled = 1`). Nothing requires it of the accounts that matter most. An `admin` password is currently the single factor protecting user creation, role assignment, password resets for every other account, database backup download, and global cross-site reads. `logistics` and `auditor` read every site's data unscoped.

Given the machinery already exists, this is a policy change more than an engineering one, and it is the single largest reduction in account-takeover risk available here.

**Effort:** ~half a day server-side, plus a small SPA flow for the forced-enrolment screen.

**Care:** Ship a documented admin recovery path first (`admin.py:255` already resets TOTP), and make sure at least two admin accounts are enrolled before enforcement flips, or you can lock yourselves out of your own console.

### 1.4 Add dependency and static scanning to CI

**What:** `pip-audit` (or `safety`) for Python, `npm audit --audit-level=high` for the SPA, `bandit` for Python SAST, and GitHub Dependabot.

**Why:** There is **none** today — `.github/workflows/` has five workflows, none of which scan dependencies or source, and there is no `.github/dependabot.yml`. The audit deliberately excluded outdated-dependency findings as out of scope, which means that whole risk category is currently unmeasured. This stack has a large surface: FastAPI, SQLAlchemy, openpyxl, xlsxwriter, fpdf2, Pillow, python-jose/PyJWT, bcrypt, React, Ant Design.

**Effort:** ~2 hours. Add to `postgres-dual-ci.yml` as a non-blocking job first, review the initial noise, then make it blocking.

**Recommendation:** Start non-blocking. A first `npm audit` run on an Ant Design tree will produce a wall of transitive advisories, most of them build-time-only, and a blocking gate on day one just teaches everyone to bypass it.

---

## Tier 2 — Before or shortly after the Hetzner cutover

### 2.1 Move the access token out of `localStorage`

**What:** Hold the access token in a JavaScript closure (memory only), relying on the existing refresh cookie to survive reloads.

**Why:** `AuthContext.tsx:38` reads the token from `localStorage`, which is readable by any script on the origin. Today that is **not** exploitable — the audit found zero XSS sinks, no `dangerouslySetInnerHTML`, and the new CSP forbids inline script. This is precisely a defense-in-depth item: it changes the consequence of a future XSS from *silent account takeover* to *session-limited damage*.

**Effort:** ~half a day. The refresh flow already exists and works, so a reload simply re-refreshes.

**Trade-off:** A full page reload costs one extra round trip. Multi-tab behaviour needs a look (each tab holds its own token; that is fine, they share the cookie).

### 2.2 Encrypt and off-site the backups

**What:** Encrypt `pg_dump` output (age or GPG) and ship it off the box on a schedule, with a documented restore drill.

**Why:** `console.py:162` writes an **unencrypted** custom-format dump to `GI_BACKUPS_DIR` on the same host as the database. That file contains every user row (bcrypt hashes, TOTP secrets, phone numbers), employee PII, and uploaded document blobs. A single host compromise takes both the live database and its history. There is also no evidence of a tested restore path.

**Effort:** ~1 day including a real restore rehearsal. Do the rehearsal — an untested backup is a hypothesis.

### 2.3 Add security headers at the API, not only at nginx

**What:** A small FastAPI middleware setting `X-Content-Type-Options`, `Referrer-Policy`, `Cache-Control: no-store` on authenticated JSON responses, and `Strict-Transport-Security`.

**Why:** All security headers currently live in `deploy/nginx.conf`. That is correct for the tunnel topology, but it means the API's own responses are bare if anything ever reaches it directly — a debug port-forward, a misconfigured second ingress, a future mobile gateway. Headers set at the origin travel with the response regardless of what fronts it.

`Cache-Control: no-store` on authenticated responses is the one with immediate value: report downloads and JSON containing site-scoped data should not sit in an intermediary or disk cache.

**Effort:** ~2 hours.

### 2.4 Tamper-evident audit log

**What:** Either a hash chain (each row stores `sha256(previous_hash || row)`) or shipping `system_audit_log` to append-only external storage.

**Why:** The audit trail is thorough and genuinely well used — the AI lane logs the SQL it ran, role changes log the before/after, refresh replay logs the family. But it lives in the same database as the data it describes, and an attacker with admin or database access can rewrite it. Right now the log proves what happened only against an adversary who did not think to edit it.

This matters most if GI Hub is ever subject to an audit or a dispute about who approved what — which, for an inventory and procurement system handling material valuations, is a realistic scenario.

**Effort:** ~1 day for the hash chain, plus a verification command.

### 2.5 Add a CSP report endpoint

**What:** `report-uri` / `report-to` on the CSP, pointed at a lightweight collector.

**Why:** The CSP shipped in PR #32 is tuned tightly (`script-src 'self'`, no `unsafe-eval`). That tightness is only safe if you find out when it blocks something legitimate — otherwise the first symptom is a user saying "the page is blank" after an unrelated frontend change. A report endpoint also becomes your earliest XSS signal.

**Effort:** ~3 hours. Consider `Content-Security-Policy-Report-Only` alongside the enforcing header when you next tighten it further.

---

## Tier 3 — Maturity, longer horizon

### 3.1 Password policy beyond length

`admin.py:39` sets `MIN_PW = 12`, applied consistently to create, reset and self-registration — good, and consistency there was itself an audit fix. But length is the only criterion, so `password1234` and `CNCEC2026CNCEC` both pass. Add a check against a breached-password list (the Pwned Passwords k-anonymity API needs no data to leave your network beyond a 5-character hash prefix), or ship `zxcvbn` for a strength estimate. **~half a day.**

### 3.2 Absolute session lifetime

Native refresh families live 90 days and slide on every rotation (`auth.py:78`), so an active device is never forced to re-authenticate. That is a deliberate, defensible warehouse-tablet decision. Consider an **absolute** cap regardless of activity — 180 days, say — so a family cannot live forever. **~2 hours.**

### 3.3 Per-user notification of security events

Send the existing WhatsApp/in-app dispatch on: password change, 2FA disabled, a login from a new device family, and admin-initiated session revocation. The notification infrastructure (`dispatch()`) already exists and is used everywhere else; this is wiring, not building. It turns the user into a detection channel for account takeover. **~half a day.**

### 3.4 Structured security logging and alerting

`system_audit_log` records events but nothing watches it. Define a small set of alertable patterns — repeated `LOGIN_FAILED` across many accounts, `SESSION_REUSE` (already a strong theft signal), `AI_QUERY` refusals, role escalations, `2FA_DISABLED` — and route them somewhere a human sees. **~1 day.**

### 3.5 Attachment content validation

`entry_docs.py:127` validates the client-supplied `Content-Type`, and the guard is **skipped entirely when the header is empty** (`if mime and not any(...)`). Today this is not exploitable — previews are restricted to `<img>` for images and `<iframe>` for `application/pdf` only, and nginx sets `nosniff` — but the check is weaker than it looks. Validate by **magic bytes** rather than the declared header, and reject rather than skip when the type is absent. Separately, `POST /entry/mtc` (`entry.py:398`) applies **no MIME allowlist at all**; it is safe only because nothing serves those blobs back, which is an accident of the current routes rather than a decision. **~half a day.**

### 3.6 Row-level security in Postgres

Site scoping is enforced in application code, carefully and with the fail-closed helpers. Postgres RLS would enforce it in the database, so a future endpoint that forgets `site_filter_applies()` cannot leak across sites. This is a significant architectural change and I would **not** do it soon — the application-layer discipline is currently good. Worth revisiting if the team grows or if multi-tenancy becomes a contractual requirement. **~1 week, high regression risk.**

### 3.7 Secrets management

Secrets live in a gitignored `deploy/.env` with a runtime warning if it is world-readable (`config.py:37`) — reasonable for a single box. If you grow past one host, move to Docker secrets, SOPS, or a managed vault, and establish a rotation schedule for the Meta token and `JWT_SECRET`. Note that rotating `JWT_SECRET` invalidates every live access token, so pair it with a documented maintenance window. **~1 day when the time comes.**

---

## Deliberately NOT recommended

Listing these so nobody spends effort re-litigating them:

- **Blocking `=` at input.** The correct boundary is the export, not the entry form. A remark legitimately starting with `-` is real, and input filters break real data while missing the paths that do not go through that form (bulk import, Excel sync, the legacy app).
- **Removing `'unsafe-inline'` from `style-src`.** Ant Design v5 is CSS-in-JS. The UI blanks without it. A nonce-based approach would require ejecting from AntD's runtime style injection — enormous effort, minimal gain.
- **`X-Frame-Options: DENY`.** The Document Library previews PDFs in an iframe pointed at `/api/entry/attachments/{id}/download`. `SAMEORIGIN` is the correct value; `DENY` breaks a working feature.
- **A WAF in front of the app.** Cloudflare already fronts this with Zero Trust Access. A second layer would mostly generate false positives against the AI NL→SQL lane, which legitimately posts SQL-shaped text.
- **Rewriting the legacy Streamlit app's SQL.** `legacy/database.py` has many f-string DDL statements, but every interpolated value is a module-level constant and that tree is frozen and unserved. Leave it.

---

## Suggested sequencing

| Phase | Items | Rough effort |
|---|---|---|
| **Now** (pre-cutover) | 1.4 CI scanning · 1.3 2FA enforcement · 1.1 shared OTP/2FA limiters | ~2 days |
| **Cutover window** | 2.2 backup encryption + restore drill · 2.3 API headers · 2.5 CSP reporting | ~2 days |
| **First month live** | 1.2 CSRF on refresh · 2.1 token out of localStorage · 3.3 security notifications | ~2 days |
| **Quarter** | 2.4 audit chain · 3.1 password policy · 3.4 alerting · 3.5 magic-byte validation | ~3 days |
| **Revisit later** | 3.2 absolute session cap · 3.6 RLS · 3.7 secrets management | as the team/infra grows |

Tier 1 is roughly **three days of work** and covers the majority of the residual risk. If only one item ships, make it **1.3 (enforce 2FA on admin and logistics)** — it is the smallest change with the largest reduction in the probability of a real compromise.

---

## One caveat on this document

These recommendations are grounded in a read of the code, not in a live penetration test or a threat model workshop with the people who run the warehouse. Two things I could not assess from source alone, and which are worth more than several items above:

1. **Who actually holds admin accounts, and how are they offboarded?** The strongest authentication in the world does not survive a shared admin password or a departed employee's live session.
2. **What is the real recovery position?** Backups exist; a *tested* restore is what makes them a control rather than a file.

Both are operational questions, and both belong on this list ahead of most of Tier 3.
